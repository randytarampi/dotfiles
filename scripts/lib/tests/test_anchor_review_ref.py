import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "anchor-review-ref.py"
SPEC = importlib.util.spec_from_file_location("anchor_review_ref", SCRIPT)
ANCHOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ANCHOR)


@pytest.fixture(autouse=True)
def test_cli_args(monkeypatch):
    monkeypatch.setattr("sys.argv", [str(SCRIPT)])


def test_changes_only_trusted_ref_and_preserves_yaml(monkeypatch, tmp_path):
    source = (
        "# keep\njobs:\n  review:\n    uses: randytarampi/dotfiles/.github/workflows/agentic-review.yml@main\n    with:\n      trusted_ref: "
        + "a" * 40
        + "\n"
    )
    target = tmp_path / "workflow.yml"
    target.write_text(source)
    monkeypatch.setattr(ANCHOR, "DISPATCHER", target)
    calls = []

    def fake_git(*args):
        calls.append(args)
        if args == ("branch", "--show-current"):
            return "main"
        if args == (
            "fetch",
            "--no-tags",
            "origin",
            "+refs/heads/main:refs/remotes/origin/main",
        ):
            return ""
        if args == ("rev-parse", "--verify", "FETCH_HEAD^{commit}"):
            return "b" * 40
        return ""

    monkeypatch.setattr(ANCHOR, "git", fake_git)
    assert ANCHOR.main() == 0
    result = target.read_text()
    assert result == source.replace("a" * 40, "b" * 40)
    assert result.count("trusted_ref:") == 1


def test_git_uses_path_resolved_executable_and_fixed_argv(monkeypatch):
    observed = {}
    executable = "/usr/bin/git"

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        return SimpleNamespace(stdout="ok\n")

    monkeypatch.setattr(ANCHOR.shutil, "which", lambda name: executable)
    monkeypatch.setattr(ANCHOR.subprocess, "run", fake_run)
    assert ANCHOR.git("status", "--short") == "ok"
    assert observed["command"] == [executable, "status", "--short"]
    assert observed["kwargs"]["cwd"] == ANCHOR.ROOT
    assert observed["kwargs"]["check"] is True
    assert "shell" not in observed["kwargs"]


def test_uses_fetched_head_not_stale_origin_main(monkeypatch, tmp_path):
    source = (
        "uses: randytarampi/dotfiles/.github/workflows/agentic-review.yml@main\ntrusted_ref: "
        + "b" * 40
        + "\n"
    )
    target = tmp_path / "workflow.yml"
    target.write_text(source)
    monkeypatch.setattr(ANCHOR, "DISPATCHER", target)
    git_outputs = {
        ("branch", "--show-current"): "main",
        ("rev-parse", "--verify", "FETCH_HEAD^{commit}"): "b" * 40,
        ("rev-parse", "--verify", "origin/main^{commit}"): "a" * 40,
    }
    monkeypatch.setattr(ANCHOR, "git", lambda *args: git_outputs.get(args, ""))
    assert ANCHOR.main() == 0
    assert target.read_text() == source


def test_idempotent_when_current_anchor_matches(monkeypatch, tmp_path):
    source = (
        "uses: randytarampi/dotfiles/.github/workflows/agentic-review.yml@main\ntrusted_ref: "
        + "b" * 40
        + "\n"
    )
    target = tmp_path / "workflow.yml"
    target.write_text(source)
    monkeypatch.setattr(ANCHOR, "DISPATCHER", target)
    git_outputs = {
        ("branch", "--show-current"): "main",
        ("rev-parse", "--verify", "FETCH_HEAD^{commit}"): "b" * 40,
    }
    monkeypatch.setattr(ANCHOR, "git", lambda *args: git_outputs.get(args, ""))
    assert ANCHOR.main() == 0
    assert target.read_text() == source


def test_rejects_non_main_branch(monkeypatch):
    monkeypatch.setattr(
        ANCHOR,
        "git",
        lambda *args: "feature" if args == ("branch", "--show-current") else "",
    )
    assert ANCHOR.main() == 1


def test_dry_run_preserves_file(monkeypatch, tmp_path):
    source = (
        "uses: randytarampi/dotfiles/.github/workflows/agentic-review.yml@main\ntrusted_ref: "
        + "a" * 40
        + "\n"
    )
    target = tmp_path / "workflow.yml"
    target.write_text(source)
    monkeypatch.setattr(ANCHOR, "DISPATCHER", target)
    calls = []

    def fake_git(*args):
        calls.append(args)
        if args == ("branch", "--show-current"):
            return "main"
        if args == ("ls-remote", "origin", "refs/heads/main"):
            return "b" * 40 + "\trefs/heads/main"
        return ""

    monkeypatch.setattr(ANCHOR, "git", fake_git)
    monkeypatch.setattr("sys.argv", [str(SCRIPT), "--dry-run"])
    assert ANCHOR.main() == 0
    assert target.read_text() == source
    assert not any(call[0] == "fetch" for call in calls)


def test_rejects_short_remote_sha(monkeypatch, tmp_path):
    target = tmp_path / "workflow.yml"
    target.write_text(
        "uses: randytarampi/dotfiles/.github/workflows/agentic-review.yml@main\ntrusted_ref: "
        + "a" * 40
        + "\n"
    )
    monkeypatch.setattr(ANCHOR, "DISPATCHER", target)
    monkeypatch.setattr(
        ANCHOR,
        "git",
        lambda *args: (
            "main"
            if args == ("branch", "--show-current")
            else "deadbeef\trefs/heads/main"
        ),
    )
    monkeypatch.setattr("sys.argv", [str(SCRIPT), "--dry-run"])
    assert ANCHOR.main() == 1


def test_rejects_non_sha_trusted_ref(monkeypatch, tmp_path):
    target = tmp_path / "workflow.yml"
    target.write_text(
        "uses: randytarampi/dotfiles/.github/workflows/agentic-review.yml@main\ntrusted_ref: deadbeef\n"
    )
    monkeypatch.setattr(ANCHOR, "DISPATCHER", target)
    monkeypatch.setattr(
        ANCHOR,
        "git",
        lambda *args: "main" if args == ("branch", "--show-current") else "b" * 40,
    )
    assert ANCHOR.main() == 1


def test_atomic_replace_failure_preserves_original_and_cleans_temp(
    monkeypatch, tmp_path
):
    target = tmp_path / "workflow.yml"
    original = b"original bytes"
    target.write_bytes(original)
    monkeypatch.setattr(
        ANCHOR.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("disk full"))
    )
    with pytest.raises(OSError, match="disk full"):
        ANCHOR.atomic_write(target, "new content")
    assert target.read_bytes() == original
    assert list(tmp_path.iterdir()) == [target]
