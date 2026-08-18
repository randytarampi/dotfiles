#!/usr/bin/env python3
"""Check for PEP 604 type hints without `from __future__ import annotations`.

PEP 604 union syntax (e.g. ``dict | None``) requires Python 3.10+ at runtime.
The ``from __future__ import annotations`` import enables PEP 563 postponed
evaluation, making the syntax work on Python 3.7+. This check catches files
that use PEP 604 syntax but lack the future import, which would fail on
Python 3.9.
"""

import argparse
import ast
import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent


def has_future_annotations(tree: ast.AST) -> bool:
    """Return True if the module imports ``from __future__ import annotations``."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            for alias in node.names:
                if alias.name == "annotations":
                    return True
    return False


def find_pep604_annotations(filepath: Path) -> list[tuple[int, str]]:
    """Return list of (lineno, snippet) for PEP 604 usage in annotation contexts."""
    try:
        source = filepath.read_text()
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return []

    if has_future_annotations(tree):
        return []

    violations: list[tuple[int, str]] = []

    def check_annotation(node, attr: str) -> None:
        ann = getattr(node, attr, None)
        if ann is None:
            return
        for sub in ast.walk(ann):
            if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.BitOr):
                line = getattr(sub, "lineno", node.lineno)
                snippet = (
                    source.splitlines()[line - 1].strip()
                    if line <= len(source.splitlines())
                    else ""
                )
                violations.append((line, snippet))

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            check_annotation(node, "returns")
            for arg in (
                *node.args.args,
                *node.args.posonlyargs,
                *node.args.kwonlyargs,
            ):
                check_annotation(arg, "annotation")
            if node.args.vararg:
                check_annotation(node.args.vararg, "annotation")
            if node.args.kwarg:
                check_annotation(node.args.kwarg, "annotation")
        elif isinstance(node, ast.AnnAssign):
            check_annotation(node, "annotation")

    return violations


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check for PEP 604 type hints without future annotations import.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="No-op (this check is read-only; --dry-run has no effect).",
    )
    args = parser.parse_args()
    _ = args  # silence unused

    files: list[Path] = []
    for pattern in ("scripts/*.py", "scripts/lib/*.py"):
        files.extend(SCRIPT_ROOT.parent.glob(pattern))
    files = sorted(set(files))

    total_violations = 0
    for f in files:
        violations = find_pep604_annotations(f)
        if violations:
            total_violations += len(violations)
            rel = f.relative_to(SCRIPT_ROOT.parent)
            for line, snippet in violations:
                print(
                    f"  {rel}:{line}: PEP 604 union type hint without `from __future__ import annotations`"
                )
                if snippet:
                    print(f"    {snippet}")

    if total_violations:
        print(
            f"\n{total_violations} violation(s) found. Add `from __future__ import annotations` to the affected file(s)."
        )
        return 1

    print("No PEP 604 violations found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
