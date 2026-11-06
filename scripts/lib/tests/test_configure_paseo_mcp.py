"""Paseo MCP owner-only configuration tests."""

import importlib.util
import json
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "configure-paseo-mcp.py"
SPEC = importlib.util.spec_from_file_location("paseo_mcp_under_test", SOURCE)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


@pytest.fixture(autouse=True)
def isolated_relay_environment(monkeypatch):
    """Host shells export the canonical ~/.env; pin the undeclared posture."""
    monkeypatch.setenv("DOTFILES_PASEO_RELAY", "0")
    for key in ("PASEO_RELAY_ENABLED", "PASEO_LISTEN", "PASEO_WEB_UI_ENABLED"):
        monkeypatch.delenv(key, raising=False)


def test_safe_defaults_only():
    assert module.plan({"version": 1}) == [
        ("daemon.mcp.enabled", True),
        ("daemon.mcp.injectIntoAgents", False),
    ]
    assert (
        module.plan({"daemon": {"mcp": {"enabled": True, "injectIntoAgents": False}}})
        == []
    )


def test_owner_collisions_fail_closed():
    with pytest.raises(ValueError, match="injection"):
        module.plan({"daemon": {"mcp": {"injectIntoAgents": True}}})
    with pytest.raises(ValueError, match="user-owned"):
        module.plan({"daemon": {"mcp": {"enabled": False}}})
    with pytest.raises(ValueError, match="object"):
        module.plan({"daemon": []})


def test_no_mutation_without_explicit_gate(monkeypatch, tmp_path):
    home = tmp_path / ".paseo"
    home.mkdir()
    config = home / "config.json"
    initial = {
        "version": 1,
        "daemon": {
            "listen": "127.0.0.1:6767",
            "relay": {"enabled": False},
            "auth": {"password": "$2b$12$" + "a" * 53},
        },
    }
    config.write_text(json.dumps(initial))
    config.chmod(0o600)
    monkeypatch.setenv("PASEO_HOME", str(home))
    monkeypatch.delenv(module.GATE, raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda _: "/bin/paseo")
    assert module.main(["--dry-run"]) == 0
    assert module.main(["--apply"]) == 1
    assert json.loads(config.read_text()) == initial
    with pytest.raises(SystemExit) as exc:
        module.main(["--ap"])
    assert exc.value.code == 2


def test_mcp_refuses_exposed_or_unauthenticated_daemon(monkeypatch, tmp_path):
    home = tmp_path / ".paseo"
    home.mkdir()
    source = home / "config.json"
    monkeypatch.setenv("PASEO_HOME", str(home))
    monkeypatch.setattr(module.shutil, "which", lambda _: "/bin/paseo")
    for daemon in (
        {
            "listen": "0.0.0.0:6767",
            "relay": {"enabled": False},
            "auth": {"password": "$2b$12$" + "a" * 53},
        },
        {
            "listen": "127.0.0.1:6767",
            "relay": {"enabled": True},
            "auth": {"password": "$2b$12$" + "a" * 53},
        },
        {"listen": "127.0.0.1:6767", "relay": {"enabled": False}},
    ):
        source.write_text(json.dumps({"version": 1, "daemon": daemon}))
        source.chmod(0o600)
        assert module.main(["--dry-run"]) == 1


def test_native_mcp_stage_and_live_reject_lossy_writes(monkeypatch, tmp_path):
    home = tmp_path / ".paseo"
    home.mkdir()
    source = home / "config.json"
    original = {"version": 1, "unmanaged": {"keep": True}}
    source.write_text(json.dumps(original))
    source.chmod(0o600)
    helpers = module.load_helpers()
    changes = module.plan(original)
    calls = []

    def native(_binary, native_home, name, value):
        calls.append(native_home)
        file = native_home / "config.json"
        data = json.loads(file.read_text())
        module.set_expected(data, name, value)
        file.write_text(json.dumps(data))
        return 0

    monkeypatch.setattr(module, "native_set", native)
    module.apply("/bin/paseo", home, source, original, changes, helpers)
    assert len(calls) == 4
    assert json.loads(source.read_text())["unmanaged"] == original["unmanaged"]
    source.write_text(json.dumps(original))
    calls.clear()

    def lossy(_binary, native_home, name, value):
        calls.append(native_home)
        file = native_home / "config.json"
        file.write_text(json.dumps({"daemon": {"mcp": {"enabled": True}}}))
        return 0

    monkeypatch.setattr(module, "native_set", lossy)
    with pytest.raises(ValueError, match="unrelated"):
        module.apply("/bin/paseo", home, source, original, changes, helpers)
    assert calls and home not in calls
    assert json.loads(source.read_text()) == original
