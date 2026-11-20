import importlib.util
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "verify-config.py"
SPEC = importlib.util.spec_from_file_location("verify_config", SCRIPT)
verify_config = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify_config)


def test_present_plist_reports_failure(tmp_path, capsys):
    plist = tmp_path / "sh.brew.caddy.plist"
    plist.touch()
    calls = []

    assert not verify_config.check_caddy_brew_agent(
        plist,
        lambda args: calls.append(args) or subprocess.CompletedProcess(args, 1),
        is_darwin=True,
        uid=123,
    )
    output = capsys.readouterr().out
    assert "✗" in output
    assert "brew services stop caddy" in output
    assert len(calls) == 1
    assert calls[0][0] == "launchctl"


def test_absent_plist_and_unloaded_agent_pass(tmp_path, capsys):
    calls = []

    def runner(args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 113)

    assert verify_config.check_caddy_brew_agent(
        tmp_path / "missing.plist", runner, is_darwin=True, uid=123
    )
    assert "✓" in capsys.readouterr().out
    assert len(calls) == 1


def test_non_darwin_skips_without_running_launchctl(tmp_path, capsys, monkeypatch):
    calls = []
    monkeypatch.setattr(verify_config.sys, "platform", "linux")

    assert not verify_config.check_caddy_brew_agent(
        tmp_path / "missing.plist", lambda args: calls.append(args)
    )
    assert "⊘" in capsys.readouterr().out
    assert calls == []
