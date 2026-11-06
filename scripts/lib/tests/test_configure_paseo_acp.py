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


def test_private_native_preflight_then_reload_preserves_other_keys(tmp_path, monkeypatch):
    home = tmp_path / ".paseo"
    home.mkdir()
    target = home / "config.json"
    original = {
        "version": 1,
        "user": {"keep": "yes"},
        "agents": {"providers": {"pi": {"apiKey": "private-canary-token"}}},
    }
    target.write_text(json.dumps(original))
    target.chmod(0o600)
    helpers = module.load_module("paseo_hermes_acp_test", "configure-paseo-hermes.py")
    desired = {"extends": "acp", "label": "Gemini", "command": ["/bin/gemini"]}
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        assert "private-canary-token" not in " ".join(args)
        return module.subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    module.apply_entries(
        "/bin/paseo", home, target, original, {"gemini": desired}, helpers
    )
    saved = json.loads(target.read_text())
    assert saved["user"] == original["user"]
    assert saved["agents"]["providers"]["pi"] == {"apiKey": "private-canary-token"}
    assert saved["agents"]["providers"]["gemini"] == desired
    assert [call[1:4] for call in calls] == [
        ["daemon", "config", "get"],
        ["daemon", "reload", "--home"],
    ]
    assert calls[0][-1] != str(home)
    assert calls[1][-1] == str(home)
    assert target.stat().st_mode & 0o777 == 0o600


def test_bad_staged_candidate_aborts_before_live_write(tmp_path, monkeypatch):
    home = tmp_path / ".paseo"
    home.mkdir()
    target = home / "config.json"
    original = {"version": 1, "user": {"keep": "yes"}}
    target.write_text(json.dumps(original))
    target.chmod(0o600)
    before = target.read_bytes()
    helpers = module.load_module("paseo_hermes_acp_stage", "configure-paseo-hermes.py")
    calls = []

    def refuse(args, **kwargs):
        calls.append(args)
        return module.subprocess.CompletedProcess(
            args, 19, stdout="apiKey=private-canary-token", stderr="invalid"
        )

    monkeypatch.setattr(module.subprocess, "run", refuse)
    with pytest.raises(ValueError, match="validation failed") as error:
        module.apply_entries(
            "/bin/paseo", home, target, original,
            {"gemini": {"extends": "acp", "label": "Gemini"}}, helpers,
        )
    assert "private-canary-token" not in str(error.value)
    assert target.read_bytes() == before
    assert len(calls) == 1
    assert calls[0][-1] != str(home)


def test_live_reload_damage_is_detected_without_rollback(tmp_path, monkeypatch):
    home = tmp_path / ".paseo"
    home.mkdir()
    target = home / "config.json"
    original = {"version": 1, "user": {"keep": "yes"}}
    target.write_text(json.dumps(original))
    target.chmod(0o600)
    helpers = module.load_module("paseo_hermes_acp_damage", "configure-paseo-hermes.py")
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[2] == "reload":
            data = json.loads(target.read_text())
            data.pop("user")
            target.write_text(json.dumps(data))
        return module.subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    with pytest.raises(ValueError, match="no rollback attempted"):
        module.apply_entries(
            "/bin/paseo", home, target, original,
            {"gemini": {"extends": "acp", "label": "Gemini"}}, helpers,
        )
    assert len(calls) == 2
    assert "user" not in json.loads(target.read_text())


def test_multiple_entries_are_one_file_save_and_one_reload(tmp_path, monkeypatch):
    home = tmp_path / ".paseo"
    home.mkdir()
    target = home / "config.json"
    original = {"version": 1, "metadata": {"keep": "exact"}}
    target.write_text(json.dumps(original))
    target.chmod(0o600)
    helpers = module.load_module("paseo_hermes_acp_batch", "configure-paseo-hermes.py")
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return module.subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    entries = {
        "gemini": {"extends": "acp", "label": "Gemini"},
        "junie": {"extends": "acp", "label": "Junie"},
    }
    module.apply_entries("/bin/paseo", home, target, original, entries, helpers)
    saved = json.loads(target.read_text())
    assert saved["metadata"] == original["metadata"]
    assert saved["agents"]["providers"] == entries
    assert len(calls) == 2


def test_nonzero_reload_is_failure_even_after_correct_save(tmp_path, monkeypatch):
    home = tmp_path / ".paseo"
    home.mkdir()
    target = home / "config.json"
    original = {"version": 1, "user": {"keep": "yes"}}
    target.write_text(json.dumps(original))
    target.chmod(0o600)
    helpers = module.load_module("paseo_hermes_acp_reload", "configure-paseo-hermes.py")

    def fake_run(args, **kwargs):
        failed = args[2] == "reload"
        return module.subprocess.CompletedProcess(
            args, 23 if failed else 0,
            stdout="apiKey=private-canary-token",
            stderr="private path /Users/private",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    with pytest.raises(ValueError, match="config saved; daemon reload failed") as error:
        module.apply_entries(
            "/bin/paseo", home, target, original,
            {"gemini": {"extends": "acp", "label": "Gemini"}}, helpers,
        )
    assert "no rollback attempted" in str(error.value)
    assert "private-canary-token" not in str(error.value)
    assert "Users/private" not in str(error.value)
    saved = json.loads(target.read_text())
    assert saved["user"] == original["user"]
    assert saved["agents"]["providers"]["gemini"]["label"] == "Gemini"


def test_relative_paseo_binary_refused_even_in_apply_preview(monkeypatch, tmp_path):
    monkeypatch.setattr(
        module.shutil,
        "which",
        lambda name: "./paseo" if name == "paseo" else "/bin/" + name,
    )
    assert module.main(["--dry-run"]) == 1
