#!/usr/bin/env python3
"""Preview or apply a literal-only, section-only migration of an owned .env.

This is distinct from deprecated-gate renaming. Unknown keys and comments stay
put; only unambiguous, template-known active assignments are relocated.
Sections are matched by title at either heading level ('# --- X ---' or
'# == X ==' nested inside an owning section); the finest documented title
wins. No shell evaluation, credential printing, or default writes.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

SECTION = re.compile(r"^# --- (.+?) ---$")
SUBSECTION = re.compile(r"^# == (.+?) ==$")
ASSIGN = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
BARE = re.compile(r"^[A-Za-z0-9_./:,+=@%-]*$")
SINGLE = re.compile(r"^'([^']*)'(?:\s+#.*)?$")
DOUBLE = re.compile(r'^"([^"\\$\x60]*)"(?:\s+#.*)?$')


def literal(value):
    """Parse a deliberately small shell-literal grammar without executing it."""
    if match := SINGLE.fullmatch(value):
        return match.group(1)
    if match := DOUBLE.fullmatch(value):
        return match.group(1)
    if " #" in value:
        value = value.split(" #", 1)[0]
    if BARE.fullmatch(value):
        return value
    raise ValueError("unsupported literal assignment")


def newline_for(raw):
    if b"\r" in raw.replace(b"\r\n", b""):
        raise ValueError("unsupported standalone carriage return")
    if not raw.endswith(b"\n"):
        raise ValueError("file must end with a newline")
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n")
    if crlf not in (0, lf):
        raise ValueError("mixed newline conventions")
    return "\r\n" if crlf else "\n"


def read_sections(text):
    """Return effective active values and active-key section positions."""
    values, positions, current, seen_sections = {}, [], "", set()
    for i, raw in enumerate(text.splitlines(keepends=True)):
        line = raw.rstrip("\r\n")
        heading = SECTION.fullmatch(line) or SUBSECTION.fullmatch(line)
        if heading:
            current = heading.group(1)
            if current in seen_sections:
                raise ValueError("duplicate section header")
            seen_sections.add(current)
            continue
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = ASSIGN.fullmatch(line)
        if match is None:
            raise ValueError("non-literal or unrecognised env statement")
        key, source = match.groups()
        if key in values:
            raise ValueError("duplicate active env key")
        values[key] = literal(source)
        positions.append((i, key, current))
    return values, positions


def template_sections(text):
    """Map every documented key to its finest documented section title."""
    target, top, sub = {}, "", ""
    for line in text.splitlines():
        if match := SECTION.fullmatch(line):
            top, sub = match.group(1), ""
            continue
        if match := SUBSECTION.fullmatch(line):
            sub = match.group(1)
            continue
        active = line.lstrip()
        if active.startswith("# "):
            active = active[2:]
        match = ASSIGN.fullmatch(active)
        if match:
            key = match.group(1)
            section = sub or top
            if key in target and target[key] != section:
                raise ValueError("conflicting canonical section")
            target[key] = section
    return target


def template_section_markers(text):
    """Map section titles to the template's own heading line style."""
    markers = {}
    for line in text.splitlines():
        if match := SECTION.fullmatch(line):
            markers[match.group(1)] = f"# --- {match.group(1)} ---"
        elif match := SUBSECTION.fullmatch(line):
            markers[match.group(1)] = f"# == {match.group(1)} =="
    return markers


def template_section_parents(text):
    """Identify the template's top-level owner for each subsection title."""
    parents, top = {}, ""
    for line in text.splitlines():
        if match := SECTION.fullmatch(line):
            top = match.group(1)
        elif match := SUBSECTION.fullmatch(line):
            title = match.group(1)
            if title in parents and parents[title] != top:
                raise ValueError("conflicting subsection ownership")
            if not top:
                raise ValueError("subsection without owning section")
            parents[title] = top
    return parents


def migrate(original, example):
    """Return new bytes plus key/section-only preview; preserve effective values."""
    # Python splitlines() recognizes separators that shell sourcing does not.
    # Reject those bytes before computing line indexes and moving assignments.
    unsupported = (
        "\x00",
        "\x0b",
        "\x0c",
        "\x1c",
        "\x1d",
        "\x1e",
        "\x85",
        "\u2028",
        "\u2029",
    )
    if any(control in original for control in unsupported):
        raise ValueError("unsupported control or Unicode line separator")
    raw = original.encode("utf-8")
    newline = newline_for(raw)
    values, active = read_sections(original)
    targets = template_sections(example)
    markers = template_section_markers(example)
    parents = template_section_parents(example)
    source_lines = original.splitlines(keepends=True)
    moved = {}
    for index, key, current in active:
        wanted = targets.get(key)
        if wanted is None or wanted == current:
            continue
        if not wanted:
            raise ValueError("canonical section is missing")
        previous = source_lines[index - 1].rstrip("\r\n") if index else ""
        previous_heading = SECTION.fullmatch(previous) or SUBSECTION.fullmatch(previous)
        if previous.lstrip().startswith("#") and not previous_heading:
            raise ValueError("ambiguous adjacent comment; manual relocation required")
        following = (
            source_lines[index + 1].rstrip("\r\n")
            if index + 1 < len(source_lines)
            else ""
        )
        following_heading = SECTION.fullmatch(following) or SUBSECTION.fullmatch(
            following
        )
        if following.lstrip().startswith("#") and not following_heading:
            raise ValueError("ambiguous following comment; manual relocation required")
        moved[index] = (key, wanted)
    if not moved:
        return raw, []

    blocks, current_name, current_lines = [], "", []
    for index, line in enumerate(source_lines):
        stripped = line.rstrip("\r\n")
        heading = SECTION.fullmatch(stripped) or SUBSECTION.fullmatch(stripped)
        if heading:
            blocks.append([current_name, current_lines])
            current_name, current_lines = heading.group(1), [line]
        elif index not in moved:
            current_lines.append(line)
    blocks.append([current_name, current_lines])

    for index, (key, wanted) in moved.items():
        destination = next((part for part in blocks if part[0] == wanted), None)
        if destination is None:
            heading = markers.get(wanted, f"# --- {wanted} ---")
            destination = [wanted, [f"{heading}{newline}"]]
            parent = parents.get(wanted)
            if parent:
                parent_index = next(
                    (i for i, block in enumerate(blocks) if block[0] == parent),
                    None,
                )
                if parent_index is None:
                    parent_heading = markers.get(parent, f"# --- {parent} ---")
                    blocks.append([parent, [f"{parent_heading}{newline}"]])
                    parent_index = len(blocks) - 1
                # Keep a newly created nested block inside its owner. Append
                # after existing sibling subsections, before the next top level.
                siblings = {
                    title for title, owning_parent in parents.items()
                    if owning_parent == parent
                }
                insert_at = parent_index + 1
                while (
                    insert_at < len(blocks)
                    and blocks[insert_at][0] in siblings
                ):
                    insert_at += 1
                blocks.insert(insert_at, destination)
            else:
                blocks.append(destination)
        last = destination[1][-1].rstrip("\r\n")
        heading = SECTION.fullmatch(last) or SUBSECTION.fullmatch(last)
        if last.lstrip().startswith("#") and not heading:
            raise ValueError("destination ends in an ambiguous comment")
        destination[1].append(source_lines[index])

    updated = "".join("".join(lines) for _, lines in blocks)
    if read_sections(updated)[0] != values:
        raise ValueError("effective values changed; refusing migration")
    return updated.encode("utf-8"), [
        (key, section) for _, (key, section) in sorted(moved.items())
    ]


def guarded_file(path):
    if os.name == "nt":
        raise ValueError("POSIX ownership checks unavailable")
    path = path.expanduser().absolute()
    if sys.platform == "darwin" and str(path).startswith("/var/"):
        path = Path("/private/var") / path.relative_to("/var")
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("env path or parent is a symlink")
    info = path.lstat()
    parent = path.parent.stat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) & 0o077
        or parent.st_uid != os.getuid()
        or stat.S_IMODE(parent.st_mode) & 0o022
    ):
        raise ValueError("env or parent is not private and owner-controlled")
    return path, info


def identity(info):
    return (
        info.st_dev,
        info.st_ino,
        info.st_uid,
        info.st_mode,
        info.st_nlink,
        info.st_mtime_ns,
        info.st_size,
    )


def apply(path, info, before, after):
    """O_EXCL private backup, compare exact source, and atomically replace.

    The final identity/byte comparison is best-effort drift detection, not a
    cross-process compare-and-swap: a concurrent writer between the last
    comparison and os.replace is overwritten without refusal. Only run
    --apply in a quiescent, owner-controlled maintenance window; recovery
    goes through the retained private backup.
    """
    backup_path = path.with_name(path.name + ".i2.bak")
    if backup_path.exists() or backup_path.is_symlink():
        raise ValueError("private backup already exists")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(backup_path, flags, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(before)
        handle.flush()
        os.fsync(handle.fileno())
    candidate = None
    try:
        fd, name = tempfile.mkstemp(prefix=".env-i2-", dir=path.parent)
        candidate = Path(name)
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), stat.S_IMODE(info.st_mode))
            handle.write(after)
            handle.flush()
            os.fsync(handle.fileno())
        current_path, current = guarded_file(path)
        if (
            current_path != path
            or identity(current) != identity(info)
            or path.read_bytes() != before
        ):
            raise ValueError("env changed before atomic replace; backup retained")
        os.replace(candidate, path)
    finally:
        if candidate is not None:
            candidate.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--env", type=Path, default=Path("~/.env"))
    parser.add_argument(
        "--example",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "dot_dotfiles/shell/.env.example",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        path, info = guarded_file(args.env)
        template = args.example.read_text(encoding="utf-8")
        before = path.read_bytes()
        after, changes = migrate(before.decode("utf-8"), template)
        if not changes:
            print("No section relocation needed; no write")
            return 0
        print(f"Planned relocations ({len(changes)}) [keys/sections only]:")
        for key, section in changes:
            print(f"  {key} -> {section}")
        if args.apply:
            apply(path, info, before, after)
            print("Applied with private .i2.bak backup; inspect before deployment")
        else:
            print("Preview only; pass --apply after owner review")
        return 0
    except UnicodeError:
        print("Refused: invalid env character encoding", file=sys.stderr)
        return 1
    except (OSError, ValueError) as error:
        print(f"Refused: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
