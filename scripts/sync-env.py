#!/usr/bin/env python3
"""Check or append missing keys from dot_dotfiles/shell/.env.example to ~/.env.

The parser is intentionally conservative: it extracts assignment keys without
evaluating shell code and never prints secret values.
"""

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ASSIGNMENT_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
COMMENTED_ASSIGNMENT_RE = re.compile(
    r"^\s*#\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*="
)


@dataclass
class EnvTemplateEntry:
    key: str
    line: str
    line_number: int


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

    with path.open("r", encoding="utf-8") as env_file:
        for line_number, line in enumerate(env_file, start=1):
            key = extract_key(line, include_commented=True)
            if not key:
                continue
            entries.append(
                EnvTemplateEntry(
                    key=key, line=line.rstrip("\n"), line_number=line_number
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


def uncomment_template_line(line: str) -> str:
    return re.sub(r"^\s*#\s?", "", line, count=1)


def append_missing(env_path: Path, missing: list[EnvTemplateEntry]) -> None:
    env_path.parent.mkdir(parents=True, exist_ok=True)
    needs_leading_newline = env_path.exists() and env_path.stat().st_size > 0

    with env_path.open("a", encoding="utf-8") as env_file:
        if needs_leading_newline:
            env_file.write("\n")
        env_file.write(
            "# --- Added by scripts/sync-env.py from dot_dotfiles/shell/.env.example ---\n"
        )
        for entry in missing:
            line = (
                entry.line if entry.line.lstrip().startswith("#") else f"# {entry.line}"
            )
            env_file.write(line + "\n")


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
        "--sync", action="store_true", help="Append missing keys to the env file"
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
            print(f"  - {entry.key} (template line {entry.line_number})")
    else:
        print("Missing keys: none")

    if stale:
        print("Keys only in env file (kept, not removed):")
        for key in stale:
            print(f"  - {key}")

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
        append_missing(env_path, missing)
        print(f"Appended {len(missing)} missing key(s) to {env_path}")
    elif args.sync:
        print("No missing keys to append")

    if template_duplicates or env_duplicates:
        return 1
    if args.strict and missing:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
