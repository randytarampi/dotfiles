"""Native Context Mode registration contract without real global installs."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[3]
MODULE = ROOT / "scripts/lib/context_mode_native.py"
spec = importlib.util.spec_from_file_location("context_mode_native", MODULE)
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


def fake_which(*binaries):
    mapping = {name: f"/fixture/bin/{name}" for name in binaries}
    return mapping.get


def test_default_off_has_no_side_effects():
    runner = Mock(side_effect=AssertionError("unexpected execution"))
    assert native.reconcile(fake_which("node", "npm", "claude"), runner, {}) == 0
    runner.assert_not_called()


def test_only_supported_host_clients(monkeypatch):
    monkeypatch.setattr(native, "vscode_present", lambda *_: False)
    assert native.selected_agents(
        fake_which("claude", "codex", "pi", "opencode", "qwen")
    ) == [
        "claude-code",
        "codex",
        "qwen",
    ]
    assert native.selected_agents(fake_which("pi", "opencode")) == []


@pytest.mark.parametrize(
    "version,expected",
    [
        ("v24.14.9", False),
        ("v24.15.0", True),
        ("v26.0.0", True),
        ("v22.16.0", False),
        ("v24.15.0-rc.1", False),
    ],
)
def test_node_version(version, expected):
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=version + "\n"))
    assert native.node_is_supported(fake_which("node"), run) is expected


def test_no_node_fails_before_any_write(monkeypatch):
    monkeypatch.setattr(native, "vscode_present", lambda *_: False)
    runner = Mock(side_effect=AssertionError("unexpected execution"))
    assert (
        native.reconcile(
            fake_which("npm", "claude"),
            runner,
            {"DOTFILES_RUN_CONTEXT_MODE_SETUP": "1"},
        )
        == 1
    )


def test_installs_latest_then_reconciles_supported_clients(monkeypatch):
    monkeypatch.setattr(native, "vscode_present", lambda *_: False)
    calls = []

    def run(args, **_kwargs):
        assert isinstance(args, list) and _kwargs.get("shell") in (None, False)
        calls.append(tuple(args))
        if args[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="v24.15.0\n")
        return SimpleNamespace(returncode=0, stdout="")

    initial = fake_which("node", "npm", "claude", "codex", "gemini", "qwen")

    def found(binary):
        if binary == "context-mode":
            return (
                "/fixture/bin/context-mode"
                if any("context-mode@latest" in c for c in calls)
                else None
            )
        return initial(binary)

    assert native.reconcile(found, run, {"DOTFILES_RUN_CONTEXT_MODE_SETUP": "1"}) == 0
    assert ("/fixture/bin/npm", "install", "--global", "context-mode@latest") in calls
    for agent in ("claude-code", "codex", "gemini", "qwen"):
        assert (
            "/fixture/bin/context-mode",
            "setup",
            "--yes",
            "--agents",
            agent,
        ) in calls
        assert (
            "/fixture/bin/context-mode",
            "doctor",
            "--agents",
            agent,
        ) in calls


def test_setup_failure_remains_fatal(monkeypatch):
    monkeypatch.setattr(native, "vscode_present", lambda *_: False)
    seen = []

    def run(args, **_kwargs):
        seen.append(tuple(args))
        return SimpleNamespace(returncode=int(args[-1] == "codex"), stdout="v26.0.0\n")

    assert (
        native.reconcile(
            fake_which("node", "npm", "context-mode", "claude", "codex"),
            run,
            {"DOTFILES_RUN_CONTEXT_MODE_SETUP": "1"},
        )
        == 1
    )
    assert any(c[-1] == "claude-code" for c in seen)
    assert any(c[-1] == "codex" for c in seen)


def test_doctor_failure_is_fatal_after_successful_setup(monkeypatch):
    monkeypatch.setattr(native, "vscode_present", lambda *_: False)
    seen = []

    def run(args, **_kwargs):
        seen.append(tuple(args))
        if args[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="v26.0.0\n")
        return SimpleNamespace(returncode=int("doctor" in args), stdout="")

    assert (
        native.reconcile(
            fake_which("node", "npm", "context-mode", "claude"),
            run,
            {"DOTFILES_RUN_CONTEXT_MODE_SETUP": "1"},
        )
        == 1
    )
    assert ("/fixture/bin/context-mode", "doctor", "--agents", "claude-code") in seen


def test_update_system_refreshes_latest(monkeypatch):
    monkeypatch.setattr(native, "vscode_present", lambda *_: False)
    calls = []

    def run(args, **_kwargs):
        calls.append(tuple(args))
        return SimpleNamespace(returncode=0, stdout="v26.0.0\n")

    which = fake_which("node", "npm", "context-mode", "claude")
    assert native.reconcile(which, run, {"DOTFILES_RUN_CONTEXT_MODE_SETUP": "1"}) == 0
    assert not any("context-mode@latest" in c for c in calls)
    calls.clear()
    assert (
        native.reconcile(
            which,
            run,
            {
                "DOTFILES_RUN_CONTEXT_MODE_SETUP": "1",
                "DOTFILES_RUN_UPDATE_SYSTEM_SETUP": "1",
            },
        )
        == 0
    )
    assert any("context-mode@latest" in c for c in calls)


def test_fleet_setup_does_not_duplicate_mcp_or_pi_plugin():
    orchestrator = (ROOT / "scripts/configure-all.sh").read_text()
    assert "DOTFILES_RUN_CONTEXT_MODE_SETUP:-0" in orchestrator
    assert "step_skipped context-mode" in orchestrator
    assert '"$CONFIG_MODE" == "global"' in orchestrator
    assert "_context_mode_args+=(--dry-run)" in orchestrator
    assert "context_mode_native.py" in orchestrator
    registry = (ROOT / "configs/mcp/global-mcps.json").read_text()
    assert '"context-mode"' not in registry
    pi = (ROOT / "scripts/configure-pi.py").read_text()
    assert '["npm:context-mode"]' in pi


def test_help_is_side_effect_free(capsys):
    with pytest.raises(SystemExit) as excinfo:
        native.main(["--help"])
    assert excinfo.value.code == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_dry_run_reports_plan_without_installing(monkeypatch, capsys):
    monkeypatch.setattr(native, "vscode_present", lambda *_: False)
    runner = Mock(side_effect=AssertionError("unexpected execution"))
    assert (
        native.reconcile(
            fake_which("node", "npm", "claude"),
            runner,
            {"DOTFILES_RUN_CONTEXT_MODE_SETUP": "1"},
            dry_run=True,
        )
        == 0
    )
    runner.assert_not_called()
    output = capsys.readouterr().out
    assert "npm install --global context-mode@latest" in output
    assert "setup --yes --agents claude-code" in output
    assert "doctor --agents claude-code" in output


def test_unknown_flag_is_a_usage_error():
    with pytest.raises(SystemExit) as excinfo:
        native.main(["--not-a-real-flag"])
    assert excinfo.value.code == 2
