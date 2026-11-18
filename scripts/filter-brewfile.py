#!/usr/bin/env python3
"""Emit a Brewfile with operator-ignored brew/cask lines removed.

`DOTFILES_BREWFILE_IGNORE` is documented as comma-separated brew/cask names.
This filter treats those names literally, so a name containing regex
metacharacters can never widen the match, and it preserves every other line
byte for byte. Validity is the preflight's job: malformed lines are copied
through untouched so the audit still refuses them.
"""

import argparse
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from brewfile_directives import IGNORABLE_KINDS, parse_line, split_ignore  # noqa: E402


def filtered_lines(path, ignored):
    """Yield the manifest's lines, dropping exactly-ignored brew/cask entries."""
    manifest = Path(path)
    if not manifest.is_file() or manifest.is_symlink():
        raise ValueError(f"Missing or symlinked Brewfile: {manifest}")
    for raw in manifest.read_text(encoding="utf-8").splitlines(keepends=True):
        parsed = parse_line(raw)
        if parsed is not None:
            kind, name = parsed
            if kind in IGNORABLE_KINDS and name in ignored:
                continue
        yield raw


def write_atomic(path, lines, force):
    target = Path(path)
    if target.is_symlink():
        raise ValueError(f"Refusing to write through a symlink: {target}")
    if target.exists() and not force:
        raise ValueError(
            f"Refusing to overwrite existing file without --force: {target}"
        )
    directory = target.parent if str(target.parent) else Path(".")
    handle, temporary = tempfile.mkstemp(
        dir=directory, prefix=f".{target.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.writelines(lines)
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="Brewfile to filter; must be a regular file, not a symlink",
    )
    parser.add_argument("--out", type=Path, required=True, help="Destination Brewfile")
    parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        metavar="NAME",
        help="Exact brew/cask name to drop; repeat for each name",
    )
    parser.add_argument(
        "--ignore-list",
        default="",
        metavar="NAMES",
        help="Comma-separated names, as stored in DOTFILES_BREWFILE_IGNORE",
    )
    parser.add_argument(
        "--force", action="store_true", help="Overwrite an existing destination"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be written without touching the destination",
    )
    args = parser.parse_args(argv)
    ignored = frozenset(args.ignore) | split_ignore(args.ignore_list)
    try:
        lines = list(filtered_lines(args.manifest, ignored))
        if args.dry_run:
            removed = len(
                args.manifest.read_text(encoding="utf-8").splitlines(True)
            ) - len(lines)
            print(
                f"filter-brewfile: would write {len(lines)} line(s) to {args.out} "
                f"({removed} removed, {len(ignored)} ignored name(s))"
            )
            return 0
        write_atomic(args.out, lines, args.force)
    except (OSError, ValueError) as exc:
        print(f"filter-brewfile: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
