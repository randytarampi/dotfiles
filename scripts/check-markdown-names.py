#!/usr/bin/env python3
"""Enforce Markdown filename conventions and verify relative Markdown links."""

import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
KEBAB_FILE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\.md\Z")
LINK = re.compile(r"!?\[[^\]\n]*\]\(([^)\n]+)\)")
INLINE_CODE = re.compile(chr(96) + "[^" + chr(96) + "]*" + chr(96))
EXCEPTIONS = {
    ".github/PULL_REQUEST_TEMPLATE.md",
    "dot_config/opencode/oh-my-opencode-slim/orchestrator_append.md",
}


def permitted_name(path: str) -> bool:
    """Return whether a Git-tracked Markdown filename follows the policy."""
    name = PurePosixPath(path).name
    if path in EXCEPTIONS or name in {"AGENTS.md", "README.md"}:
        return True
    if name == "SKILL.md" and "/skills/" in f"/{path}":
        return True
    return KEBAB_FILE.fullmatch(name) is not None


def markdown_link_failures(document: Path, root: Path) -> list[tuple[int, str]]:
    """Check only local Markdown links, never network URLs or code examples."""
    failures = []
    in_fence = False
    try:
        content = document.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return [(0, f"unreadable Markdown: {exc}")]
    for number, line in enumerate(content.splitlines(), 1):
        if line.lstrip().startswith((chr(96) * 3, "~" * 3)):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for found in LINK.finditer(INLINE_CODE.sub("", line)):
            raw = found.group(1).strip().strip("<>")
            target = raw.split("#", 1)[0].split("?", 1)[0]
            if not target or target.startswith(("#", "/", "~", "//")):
                continue
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
                continue
            if not target.lower().endswith(".md"):
                continue
            path = (document.parent / target).resolve()
            if not path.is_relative_to(root.resolve()) or not path.is_file():
                failures.append((number, target))
    return failures


def tracked_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [
        part.decode("utf-8", errors="surrogateescape")
        for part in result.stdout.split(b"\0")
        if part
    ]


def main() -> int:
    try:
        files = tracked_files(ROOT)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Cannot inspect tracked files: {exc}", file=sys.stderr)
        return 1
    violations = 0
    for filename in files:
        if not filename.lower().endswith(".md"):
            continue
        if not permitted_name(filename):
            print(f"Non-kebab-case Markdown filename: {filename}", file=sys.stderr)
            violations += 1
        document = ROOT / filename
        for line, target in markdown_link_failures(document, ROOT):
            print(
                f"Broken local Markdown link: {filename}:{line}: {target}",
                file=sys.stderr,
            )
            violations += 1
    if violations:
        print(
            f"Markdown convention check failed: {violations} finding(s)",
            file=sys.stderr,
        )
        return 1
    print("Markdown naming and local-link checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
