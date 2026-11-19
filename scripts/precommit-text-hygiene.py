#!/usr/bin/env python3
"""Staged-file text hygiene checks for the commit path.

Reads the file paths supplied by pre-commit, so only what is actually staged is
examined. Never writes: the commit path validates, it does not mutate. Whole-tree
rewriting belongs to `make fix`, which a developer runs deliberately.

Markdown is exempt from the trailing-whitespace rule because two trailing spaces
are a hard line break there.
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKDOWN_SUFFIXES = {".md", ".markdown"}
CONFLICT_MARKERS = ("<<<<<<<", ">>>>>>>")


def _looks_binary(path: Path) -> bool:
    try:
        return b"\0" in path.read_bytes()[:4096]
    except OSError:
        return True


def check(path: Path) -> list[str]:
    problems: list[str] = []
    if _looks_binary(path) or path.suffix.lower() in MARKDOWN_SUFFIXES:
        return problems
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return [f"{path}: unreadable ({exc.strerror or exc})"]

    if raw and not raw.endswith(b"\n"):
        problems.append(f"{path}: missing final newline")

    text = raw.decode("utf-8", errors="replace")
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.rstrip()
        if line != stripped:
            problems.append(f"{path}:{number}: trailing whitespace")
        if line.startswith(CONFLICT_MARKERS) or line.rstrip() == "=======":
            problems.append(f"{path}:{number}: merge conflict marker")
    return problems


def main(argv: list[str]) -> int:
    problems: list[str] = []
    for name in argv:
        path = Path(name)
        if path.is_file():
            problems.extend(check(path))
    if problems:
        print("Staged-file hygiene failed:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print(
            "Fix with `make fix` (whole-tree, deliberate), then re-stage.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
