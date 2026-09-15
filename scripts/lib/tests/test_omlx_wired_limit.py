"""Tests for scripts/configure-omlx-wired-limit.py."""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]

HERMETIC_ENV_VARS = (
    "DOTFILES_RUN_OMLX_SETUP",
    "OMLX_WIRED_LIMIT_MB",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    # The developer shell may source ~/.env before pytest; gate/limit vars
    # must not leak into tests that assert specific gate states. main()
    # re-loads ~/.env via env.load_env(), so patch that too.
    for name in HERMETIC_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("env.load_env", lambda *a, **k: False)


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / name)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def wired(monkeypatch):
    module = load_script("configure-omlx-wired-limit.py")
    monkeypatch.setattr(module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(module.platform, "machine", lambda: "arm64")
    return module


def test_gate_off_skips(monkeypatch, wired, caplog):
    monkeypatch.setattr(wired, "_physical_ram_mb", lambda: 49152)
    with (
        patch.object(wired, "apply_sysctl") as apply_mock,
        patch.object(wired, "install_plist") as install_mock,
        patch.object(sys, "argv", ["configure-omlx-wired-limit.py"]),
    ):
        wired.main()
    apply_mock.assert_not_called()
    install_mock.assert_not_called()


def test_default_limit_applied_and_installed(monkeypatch, wired):
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    monkeypatch.setattr(wired, "_physical_ram_mb", lambda: 49152)
    with (
        patch.object(wired, "apply_sysctl") as apply_mock,
        patch.object(wired, "install_plist") as install_mock,
        patch.object(sys, "argv", ["configure-omlx-wired-limit.py"]),
    ):
        wired.main()
    apply_mock.assert_called_once_with(40960, dry_run=False)
    install_mock.assert_called_once()
    args, kwargs = install_mock.call_args
    assert args[1] == 40960


def test_custom_limit_respected(monkeypatch, wired):
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    monkeypatch.setenv("OMLX_WIRED_LIMIT_MB", "43008")
    monkeypatch.setattr(wired, "_physical_ram_mb", lambda: 49152)
    with (
        patch.object(wired, "apply_sysctl") as apply_mock,
        patch.object(wired, "install_plist") as install_mock,
        patch.object(sys, "argv", ["configure-omlx-wired-limit.py"]),
    ):
        wired.main()
    apply_mock.assert_called_once_with(43008, dry_run=False)


def test_limit_above_90pct_ram_refused(monkeypatch, wired):
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    monkeypatch.setenv("OMLX_WIRED_LIMIT_MB", "49152")
    monkeypatch.setattr(wired, "_physical_ram_mb", lambda: 49152)
    with pytest.raises(SystemExit):
        wired.main()


def test_invalid_limit_rejected(monkeypatch, wired):
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    monkeypatch.setenv("OMLX_WIRED_LIMIT_MB", "huge")
    with pytest.raises(SystemExit):
        wired.main()


def test_plist_content_shape(wired):
    plist = wired.build_plist(40960)
    assert wired.LAUNCHD_LABEL in plist
    assert "iogpu.wired_limit_mb=40960" in plist
    assert "RunAtLoad" in plist
    assert "<dict>" in plist


def test_apply_sysctl_noop_when_converged(monkeypatch, wired):
    monkeypatch.setattr(wired, "_read_wired_limit", lambda: 40960)
    assert wired.apply_sysctl(40960, dry_run=False) is False


def test_apply_sysctl_dry_run_does_not_write(monkeypatch, wired):
    monkeypatch.setattr(wired, "_read_wired_limit", lambda: 0)
    ran = []
    monkeypatch.setattr(
        wired.subprocess, "run", lambda *a, **k: ran.append(a) or _fake_run()
    )
    assert wired.apply_sysctl(40960, dry_run=True) is True
    assert not ran


class _FakeCompleted:
    returncode = 0
    stdout = "ok"
    stderr = ""


def _fake_run():
    return _FakeCompleted()


def test_apply_sysctl_escalates_via_osascript(monkeypatch, wired):
    state = {"applied": False}
    calls = []

    def fake_run(cmd, **kwargs):
        joined = " ".join(cmd)
        calls.append(cmd)
        fake = _FakeCompleted()
        if "osascript" in joined:
            # Escalated apply succeeded — reflect it in subsequent reads.
            state["applied"] = True
            return fake
        if "sudo" in joined and state["applied"]:
            return fake
        # sudo -n fails (no cached credentials) and reads report 0 until the
        # escalated path runs.
        fake.returncode = 1
        fake.stdout = "0"
        return fake

    monkeypatch.setattr(wired.subprocess, "run", fake_run)
    monkeypatch.setattr(
        wired, "_read_wired_limit", lambda: state["applied"] and 40960 or 0
    )
    assert wired.apply_sysctl(40960, dry_run=False) is True
    assert any("osascript" in " ".join(cmd) for cmd in calls)
