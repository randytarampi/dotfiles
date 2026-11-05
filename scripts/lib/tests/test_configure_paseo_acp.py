"""Bounded ACP plan tests; never launch real agents or modify Paseo state."""

import importlib.util
import json
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "configure-paseo-acp.py"
SPEC = importlib.util.spec_from_file_location("paseo_acp_under_test", SOURCE)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def candidate_specs():
    return {
        "gemini": {"command": "gemini", "args": ["--acp"]},
        "junie": {"command": "junie", "args": ["--acp", "true"]},
        "cortex": {
            "command": "cortex",
            "args": ["acp", "serve", "-c", "${CORTEX_CONNECTION}"],
        },
    }


def test_available_and_unsupported_are_distinct():
    found, skipped = module.discovered(
        candidate_specs(), lambda command: "/usr/bin/" + command
    )
    assert found["gemini"]["command"] == ["/usr/bin/gemini", "--acp"]
    assert found["junie"]["extends"] == "acp"
    assert "cortex" not in found
    assert "cortex" in skipped


def test_missing_binary_and_unresolved_command_refused():
    found, skipped = module.discovered(candidate_specs(), lambda command: None)
    assert not found
    assert "gemini" in skipped


def test_no_overwrite_or_implicit_native_duplicates():
    found = {
        "gemini": {
            "extends": "acp",
            "label": "Gemini",
            "command": ["/bin/gemini", "--acp"],
        }
    }
    assert list(module.desired_entries({"agents": {"providers": {}}}, found, ())) == [
        "gemini"
    ]
    assert (
        module.desired_entries(
            {"agents": {"providers": {"gemini": found["gemini"]}}}, found, ()
        )
        == {}
    )
    with pytest.raises(ValueError, match="collision"):
        module.desired_entries(
            {"agents": {"providers": {"gemini": {"custom": True}}}}, found, ()
        )
    with pytest.raises(ValueError, match="unknown"):
        module.desired_entries({}, found, ("opencode",))
    with pytest.raises(ValueError, match="eligible"):
        module.desired_entries({}, found, ("cursor",))


def test_applies_only_when_gate_and_exact_user_intent_present(monkeypatch, tmp_path):
    monkeypatch.setattr(module.shutil, "which", lambda name: "/bin/" + name)
    monkeypatch.setattr(
        module,
        "discovered",
        lambda *_: (
            {
                "gemini": {
                    "extends": "acp",
                    "label": "Gemini",
                    "command": ["/bin/gemini", "--acp"],
                }
            },
            {},
        ),
    )
    home = tmp_path / ".paseo"
    home.mkdir()
    config = home / "config.json"
    config.write_text('{"version":1}')
    config.chmod(0o600)
    monkeypatch.setenv("PASEO_HOME", str(home))
    monkeypatch.delenv(module.GATE, raising=False)
    assert module.main(["--dry-run"]) == 0
    assert config.read_text() == '{"version":1}'
    assert module.main(["--apply"]) == 1
    assert config.read_text() == '{"version":1}'
    with pytest.raises(SystemExit) as exc:
        module.main(["--unknown"])
    assert exc.value.code == 2


def test_cursor_environment_contract_is_never_silently_dropped():
    specs = {
        "cursor": {
            "command": "cursor-agent",
            "args": ["acp"],
            "env": {"CURSOR_API_KEY": "${CURSOR_API_KEY}"},
        }
    }
    found, skipped = module.discovered(specs, lambda _: "/bin/cursor-agent")
    assert "cursor" not in found
    assert "environment" in skipped["cursor"]


def test_discovery_preserves_stable_executable_symlink(monkeypatch):
    monkeypatch.setattr(module.os.path, "realpath", lambda _: "/versioned/bin")
    found, _ = module.discovered(
        {"gemini": {"command": "gemini", "args": ["--acp"]}},
        lambda _: "/stable/bin/gemini",
    )
    assert found["gemini"]["command"][0] == "/stable/bin/gemini"


def test_native_preflight_and_live_readback_preserve_other_keys(tmp_path, monkeypatch):
    home = tmp_path / ".paseo"
    home.mkdir()
    target = home / "config.json"
    original = {"version": 1, "user": {"keep": "yes"}}
    target.write_text(json.dumps(original))
    target.chmod(0o600)
    helpers = module.load_module("paseo_hermes_acp_test", "configure-paseo-hermes.py")
    desired = {"extends": "acp", "label": "Gemini", "command": ["/bin/gemini", "--acp"]}
    calls = []

    def native(_binary, native_home, name, value):
        calls.append((native_home, name))
        file = native_home / "config.json"
        data = json.loads(file.read_text())
        data.setdefault("agents", {}).setdefault("providers", {})[name] = value
        file.write_text(json.dumps(data))
        return 0

    monkeypatch.setattr(module, "run_set", native)
    module.apply_entries(
        "/bin/paseo", home, target, original, {"gemini": desired}, helpers
    )
    data = json.loads(target.read_text())
    assert data["user"] == original["user"]
    assert data["agents"]["providers"]["gemini"] == desired
    assert len(calls) == 2


def test_native_lossy_stage_aborts_before_live_write(tmp_path, monkeypatch):
    home = tmp_path / ".paseo"
    home.mkdir()
    target = home / "config.json"
    original = {"version": 1, "user": {"keep": "yes"}}
    target.write_text(json.dumps(original))
    target.chmod(0o600)
    helpers = module.load_module("paseo_hermes_acp_lossy", "configure-paseo-hermes.py")
    calls = []

    def lossy(_binary, native_home, name, value):
        calls.append(native_home)
        file = native_home / "config.json"
        file.write_text(json.dumps({"agents": {"providers": {name: value}}}))
        return 0

    monkeypatch.setattr(module, "run_set", lossy)
    with pytest.raises(ValueError, match="unrelated"):
        module.apply_entries(
            "/bin/paseo",
            home,
            target,
            original,
            {
                "gemini": {
                    "extends": "acp",
                    "label": "Gemini",
                    "command": ["/bin/gemini"],
                }
            },
            helpers,
        )
    assert calls and home not in calls
    assert json.loads(target.read_text()) == original


def test_relative_paseo_binary_refused_even_in_apply_preview(monkeypatch, tmp_path):
    monkeypatch.setattr(
        module.shutil,
        "which",
        lambda name: "./paseo" if name == "paseo" else "/bin/" + name,
    )
    assert module.main(["--dry-run"]) == 1
