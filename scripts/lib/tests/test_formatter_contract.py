"""Contract tests for the single formatter authority.

`make fix` and `make lint` used to resolve Black independently: `fix` used
whatever `black` was on PATH and silently skipped formatting when it was
absent, while `lint` used the Poetry-pinned binary. A PATH Black of a
different version could therefore reformat the tree into a state that lint
rejected, and a missing Black could let unformatted code through with a
green `fix`.

These tests pin the invariant so the divergence cannot be reintroduced
without failing the suite.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
MAKEFILE = REPO_ROOT / "Makefile"


def _recipe(target: str) -> str:
    """Return the recipe body for a top-level Make target."""
    lines = MAKEFILE.read_text(encoding="utf-8").splitlines()
    body: list[str] = []
    inside = False
    for line in lines:
        if re.match(rf"^{re.escape(target)}:", line):
            inside = True
            continue
        if inside:
            if line.startswith(("\t", " ")) or not line.strip():
                body.append(line)
                continue
            break
    assert body, f"target {target!r} has no recipe in Makefile"
    return "\n".join(body)


def test_fix_and_lint_share_one_black_resolver():
    """Both targets must invoke the same pinned formatter, not PATH Black."""
    fix = _recipe("fix")
    lint = _recipe("lint")
    assert "$(POETRY_BLACK)" in fix, "make fix must use the pinned formatter"
    assert "$(POETRY_BLACK)" in lint, "make lint must use the pinned formatter"
    assert "poetry run black" not in fix.replace("$(POETRY_BLACK)", "")
    assert "poetry run black" not in lint.replace("$(POETRY_BLACK)", "")


def test_pinned_formatter_defaults_to_poetry():
    text = MAKEFILE.read_text(encoding="utf-8")
    assert re.search(r"^POETRY_BLACK\s*\?=\s*poetry run black\s*$", text, re.M)


def test_fix_does_not_skip_missing_formatter():
    """A missing formatter must fail, never 'skip = success'."""
    fix = _recipe("fix")
    assert "command -v black" not in fix, "make fix must not probe PATH Black"
    assert (
        "skipping python formatting" not in fix
    ), "make fix must not silently skip Python formatting"
    assert re.search(
        r"\|\|\s*\{", fix
    ), "make fix must fail closed when the pinned formatter cannot run"
    assert "exit 1" in fix, "make fix must exit non-zero on formatter failure"


def test_lint_checks_without_writing():
    lint = _recipe("lint")
    assert "--check" in lint, "make lint must run the formatter in check mode"
    assert "--diff" in lint, "make lint should surface the diff it rejects"


def test_skill_does_not_recommend_path_black():
    skill = (
        REPO_ROOT / "configs" / "skills" / "dotfiles-verify" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "Use the installed `black` executable" not in skill
    assert "poetry run black" in skill
