"""Contract tests for the commit path.

The commit transaction must be fast, staged-only and non-mutating. It used to
run whole-tree `make fix` (a mutator) and whole-repo `make lint` through two
`always_run` hooks, which charged every commit for validation it could not
affect and could rewrite or strand unstaged work.

These tests fail if a whole-tree mutating hook is reintroduced.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG = REPO_ROOT / ".pre-commit-config.yaml"
HOOK_SCRIPT = REPO_ROOT / "scripts" / "precommit-text-hygiene.py"

MUTATING_TARGETS = ("make fix", "make lint")
CHECK_ONLY_FLAGS = {
    "python-format-check": "--check",
    "shell-format-check": "-d",
}


def _hooks() -> list[dict]:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    hooks: list[dict] = []
    for repo in config["repos"]:
        hooks.extend(repo.get("hooks", []))
    return hooks


def test_no_whole_tree_hook():
    for hook in _hooks():
        assert (
            hook.get("always_run") is not True
        ), f"{hook['id']} uses always_run, which reintroduces whole-transaction cost"


def test_no_hook_invokes_make_targets():
    for hook in _hooks():
        entry = str(hook.get("entry", ""))
        for target in MUTATING_TARGETS:
            assert target not in entry, (
                f"{hook['id']} invokes {target!r}; whole-tree validation belongs in "
                "CI and `make verify`, not the commit path"
            )


def test_every_hook_is_staged_only():
    for hook in _hooks():
        assert (
            hook.get("pass_filenames") is True
        ), f"{hook['id']} must receive filenames so only staged content is checked"


def test_formatter_hooks_are_check_only():
    by_id = {hook["id"]: hook for hook in _hooks()}
    for hook_id, flag in CHECK_ONLY_FLAGS.items():
        assert hook_id in by_id, f"missing {hook_id} hook"
        assert flag in str(
            by_id[hook_id]["entry"]
        ), f"{hook_id} must run in check-only mode ({flag})"


def test_text_hygiene_script_never_writes(tmp_path: Path):
    """The hook must report, not repair: staged content stays untouched."""
    target = tmp_path / "sample.py"
    original = "x = 1   \n"  # trailing whitespace, and no final newline
    target.write_bytes(original.encode())
    result = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "trailing whitespace" in result.stderr
    assert target.read_bytes() == original.encode(), "hook must not rewrite the file"


def test_text_hygiene_accepts_clean_file(tmp_path: Path):
    target = tmp_path / "clean.py"
    target.write_text("x = 1\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_text_hygiene_exempts_markdown_hard_breaks(tmp_path: Path):
    target = tmp_path / "note.md"
    target.write_text("line one  \nline two\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, "Markdown two-space hard breaks must be allowed"
