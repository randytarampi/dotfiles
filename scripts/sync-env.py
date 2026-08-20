#!/usr/bin/env python3
"""Check or sync ~/.env keys from dot_dotfiles/shell/.env.example.

The parser is intentionally conservative: it extracts assignment keys without
evaluating shell code and never prints secret values.

Sync mode inserts missing keys into their correct section (matching the
section header from .env.example), not as a flat block at the bottom.
Check mode reports missing keys, stale keys, duplicates, and section mismatches.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ASSIGNMENT_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
COMMENTED_ASSIGNMENT_RE = re.compile(
    r"^\s*#\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*="
)
SECTION_RE = re.compile(r"^\s*#\s*---\s+(.+?)\s+---\s*$")


@dataclass
class EnvTemplateEntry:
    key: str
    line: str
    line_number: int
    section: str = ""


@dataclass
class EnvLine:
    """A line in ~/.env with its associated section context."""

    line: str
    line_number: int
    key: str | None = None
    section: str = ""  # The section header this line falls under


def extract_key(line: str, include_commented: bool) -> str | None:
    match = ASSIGNMENT_RE.match(line)
    if match:
        return match.group(1)
    if include_commented:
        match = COMMENTED_ASSIGNMENT_RE.match(line)
        if match:
            return match.group(1)
    return None


def parse_template(path: Path) -> tuple[list[EnvTemplateEntry], dict[str, list[int]]]:
    entries: list[EnvTemplateEntry] = []
    occurrences: dict[str, list[int]] = {}
    current_section = ""

    with path.open("r", encoding="utf-8") as env_file:
        for line_number, line in enumerate(env_file, start=1):
            section_match = SECTION_RE.match(line)
            if section_match:
                current_section = section_match.group(1).strip()
                continue
            key = extract_key(line, include_commented=True)
            if not key:
                continue
            entries.append(
                EnvTemplateEntry(
                    key=key,
                    line=line.rstrip("\n"),
                    line_number=line_number,
                    section=current_section,
                )
            )
            occurrences.setdefault(key, []).append(line_number)

    return entries, {key: lines for key, lines in occurrences.items() if len(lines) > 1}


def parse_env(path: Path) -> tuple[set[str], dict[str, list[int]]]:
    keys: set[str] = set()
    occurrences: dict[str, list[int]] = {}

    if not path.exists():
        return keys, occurrences

    with path.open("r", encoding="utf-8") as env_file:
        for line_number, line in enumerate(env_file, start=1):
            key = extract_key(line, include_commented=True)
            if not key:
                continue
            keys.add(key)
            occurrences.setdefault(key, []).append(line_number)

    return keys, {key: lines for key, lines in occurrences.items() if len(lines) > 1}


def parse_env_with_sections(path: Path) -> tuple[list[EnvLine], dict[str, str]]:
    """Parse ~/.env returning lines with section context and a key→section map."""
    lines: list[EnvLine] = []
    key_to_section: dict[str, str] = {}
    current_section = ""

    if not path.exists():
        return lines, key_to_section

    with path.open("r", encoding="utf-8") as env_file:
        for line_number, line in enumerate(env_file, start=1):
            stripped = line.rstrip("\n")
            section_match = SECTION_RE.match(stripped)
            if section_match:
                current_section = section_match.group(1).strip()
                lines.append(
                    EnvLine(
                        line=stripped,
                        line_number=line_number,
                        key=None,
                        section=current_section,
                    )
                )
                continue
            key = extract_key(stripped, include_commented=True)
            lines.append(
                EnvLine(
                    line=stripped,
                    line_number=line_number,
                    key=key,
                    section=current_section,
                )
            )
            if key and key not in key_to_section:
                key_to_section[key] = current_section

    return lines, key_to_section


def uncomment_template_line(line: str) -> str:
    return re.sub(r"^\s*#\s?", "", line, count=1)


def build_section_map(
    template_entries: list[EnvTemplateEntry],
) -> dict[str, list[EnvTemplateEntry]]:
    """Group template entries by section, preserving order."""
    sections: dict[str, list[EnvTemplateEntry]] = {}
    seen_keys: set[str] = set()
    for entry in template_entries:
        if entry.key in seen_keys:
            continue
        seen_keys.add(entry.key)
        sections.setdefault(entry.section, []).append(entry)
    return sections


def _format_entry(entry: EnvTemplateEntry) -> str:
    """Format a template entry as a commented line for insertion into ~/.env."""
    return entry.line if entry.line.lstrip().startswith("#") else f"# {entry.line}"


def insert_missing(
    env_path: Path,
    missing: list[EnvTemplateEntry],
    template_entries: list[EnvTemplateEntry],
) -> None:
    """Insert missing keys into their correct sections in ~/.env.

    Reads the existing ~/.env, splits it into section blocks (header + content
    lines), appends missing keys to the end of each matching section, and
    creates new sections at the right position for keys whose section doesn't
    exist yet. Preserves all existing content and blank-line spacing.
    """
    env_lines, _ = parse_env_with_sections(env_path)

    # Group missing keys by their template section
    missing_by_section: dict[str, list[EnvTemplateEntry]] = {}
    seen: set[str] = set()
    for entry in missing:
        if entry.key in seen:
            continue
        seen.add(entry.key)
        missing_by_section.setdefault(entry.section, []).append(entry)

    # Build section order from the template (for positioning new sections)
    template_section_order: list[str] = []
    template_seen: set[str] = set()
    for entry in template_entries:
        if entry.section and entry.section not in template_seen:
            template_section_order.append(entry.section)
            template_seen.add(entry.section)

    def section_rank(section_name: str) -> int:
        try:
            return template_section_order.index(section_name)
        except ValueError:
            return len(template_section_order)

    # Split env_lines into blocks: list of (section_name, [content_lines])
    # A section block starts at a section header and includes all lines until
    # the next section header (including trailing blank lines).
    blocks: list[tuple[str, list[str]]] = []
    current_section = ""
    current_lines: list[str] = []
    existing_section_order: list[str] = []

    for el in env_lines:
        # Detect section headers — EnvLine with key=None and section set
        is_header = el.key is None and el.section and el.line.startswith("# ---")
        if is_header:
            # Flush previous block
            if current_section or current_lines:
                blocks.append((current_section, current_lines))
                if current_section and current_section not in existing_section_order:
                    existing_section_order.append(current_section)
            current_section = el.section
            current_lines = [el.line]
        else:
            current_lines.append(el.line)

    # Flush last block
    if current_section or current_lines:
        blocks.append((current_section, current_lines))
        if current_section and current_section not in existing_section_order:
            existing_section_order.append(current_section)

    # Pre-header content (lines before first section header) is in blocks[0] with section=""
    pre_header_block = blocks[0] if blocks and blocks[0][0] == "" else None

    # Append missing keys to existing section blocks
    for i, (section_name, content_lines) in enumerate(blocks):
        if section_name in missing_by_section:
            entries = missing_by_section.pop(section_name)
            # Strip trailing blank lines from the block, add keys, then add one blank
            while content_lines and content_lines[-1].strip() == "":
                content_lines.pop()
            content_lines.append("")  # separator
            for entry in entries:
                content_lines.append(_format_entry(entry))
            blocks[i] = (section_name, content_lines)

    # Create new sections for remaining missing keys
    new_sections = sorted(missing_by_section.keys(), key=section_rank)

    for new_section in new_sections:
        entries = missing_by_section.pop(new_section)
        new_block = (
            new_section,
            [f"# --- {new_section} ---"] + [_format_entry(e) for e in entries],
        )

        # Find insertion position: after the last existing section with lower rank
        insert_after_rank = section_rank(new_section)
        insert_pos = len(blocks)
        for i in range(len(blocks) - 1, -1, -1):
            block_section = blocks[i][0]
            if block_section and section_rank(block_section) < insert_after_rank:
                insert_pos = i + 1
                break
        blocks.insert(insert_pos, new_block)

    # Rebuild the file from blocks, ensuring single blank line between sections
    output_lines: list[str] = []
    for section_name, content_lines in blocks:
        # Ensure block doesn't start with blank lines (except pre-header)
        if section_name:
            while content_lines and content_lines[0].strip() == "":
                content_lines.pop(0)
        # Ensure exactly one trailing blank line between sections
        while content_lines and content_lines[-1].strip() == "":
            content_lines.pop()
        if content_lines:
            output_lines.extend(content_lines)
            output_lines.append("")

    # Remove trailing blank line (file ends with single \n)
    while output_lines and output_lines[-1] == "":
        output_lines.pop()

    env_path.parent.mkdir(parents=True, exist_ok=True)
    with env_path.open("w", encoding="utf-8") as env_file:
        env_file.write("\n".join(output_lines) + "\n")


def check_sections(
    env_path: Path,
    template_entries: list[EnvTemplateEntry],
) -> list[str]:
    """Check that each key in ~/.env is in the same section as in .env.example.

    Returns a list of mismatch descriptions.
    """
    _, env_key_sections = parse_env_with_sections(env_path)

    # Build template key → section map (first occurrence wins)
    template_key_sections: dict[str, str] = {}
    for entry in template_entries:
        if entry.key not in template_key_sections:
            template_key_sections[entry.key] = entry.section

    mismatches: list[str] = []
    for key, env_section in sorted(env_key_sections.items()):
        template_section = template_key_sections.get(key)
        if template_section is None:
            continue  # Key only in env, not in template
        if env_section != template_section:
            mismatches.append(
                f"  - {key}: in '{env_section}' in ~/.env, but in '{template_section}' in .env.example"
            )

    return mismatches


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check or sync ~/.env keys from .env.example"
    )
    parser.add_argument("--example", default="dot_dotfiles/shell/.env.example")
    parser.add_argument("--env", default=os.path.expanduser("~/.env"))
    parser.add_argument(
        "--check", action="store_true", help="Report drift without modifying files"
    )
    parser.add_argument(
        "--sync", action="store_true", help="Insert missing keys into the env file"
    )
    parser.add_argument(
        "--strict", action="store_true", help="Exit non-zero when keys are missing"
    )
    args = parser.parse_args()

    if args.check == args.sync:
        parser.error("choose exactly one of --check or --sync")

    example_path = Path(args.example).expanduser()
    env_path = Path(args.env).expanduser()

    if not example_path.is_file():
        print(f"ERROR: template not found: {example_path}", file=sys.stderr)
        return 2

    template_entries, template_duplicates = parse_template(example_path)
    env_keys, env_duplicates = parse_env(env_path)

    seen: set[str] = set()
    missing: list[EnvTemplateEntry] = []
    for entry in template_entries:
        if entry.key in seen:
            continue
        seen.add(entry.key)
        if entry.key not in env_keys:
            missing.append(entry)

    stale = sorted(env_keys - seen)

    print(f"Template: {example_path}")
    print(f"Env file: {env_path}")
    print(f"Template keys: {len(seen)}")
    print(f"Env keys: {len(env_keys)}")

    if missing:
        print("Missing keys:")
        for entry in missing:
            print(f"  - {entry.key} (section: {entry.section or 'none'})")
    else:
        print("Missing keys: none")

    if stale:
        print("Keys only in env file (kept, not removed):")
        for key in stale:
            print(f"  - {key}")

    # Section check (runs in both --check and --sync modes for visibility)
    section_mismatches = check_sections(env_path, template_entries)
    if section_mismatches:
        print("Section mismatches (key in different section than .env.example):")
        for mismatch in section_mismatches:
            print(mismatch)

    for label, duplicates in [
        ("Template", template_duplicates),
        ("Env", env_duplicates),
    ]:
        if duplicates:
            print(f"{label} duplicate keys:")
            for key, lines in duplicates.items():
                joined = ", ".join(str(line) for line in lines)
                print(f"  - {key}: lines {joined}")

    if args.sync and missing:
        insert_missing(env_path, missing, template_entries)
        print(f"Inserted {len(missing)} missing key(s) into {env_path}")
    elif args.sync:
        print("No missing keys to insert")

    if template_duplicates or env_duplicates:
        return 1
    if args.strict and missing:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
