import copy
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "configure-paseo-hermes.py"
SPEC = importlib.util.spec_from_file_location("configure_paseo_hermes", SCRIPT)
assert SPEC and SPEC.loader
paseo_hermes = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(paseo_hermes)

PROVIDER = paseo_hermes.provider("/bin/hermes")


def test_provider_uses_absolute_binary_path():
    obj = paseo_hermes.provider("/opt/hermes/bin/hermes")
    assert obj["command"] == ["/opt/hermes/bin/hermes", "acp"]
    assert obj["extends"] == "acp"


def test_provider_addition_and_preservation():
    assert (
        paseo_hermes.classify(
            {"agents": {"providers": {"pi": {"label": "user"}}}}, PROVIDER
        )
        == "absent"
    )
    data = {"agents": {"providers": {"pi": {"label": "user"}, "hermes": PROVIDER}}}
    assert paseo_hermes.classify(data, PROVIDER) == "unchanged"
    assert data["agents"]["providers"]["pi"] == {"label": "user"}


def test_collision_and_malformed_state_refused():
    for data in (
        {"agents": {"providers": {"hermes": {"command": ["custom"]}}}},
        {"agents": []},
        {"agents": {"providers": []}},
        [],
    ):
        with pytest.raises(ValueError):
            paseo_hermes.classify(data, PROVIDER)


def test_default_no_mutation(monkeypatch, tmp_path):
    home = tmp_path / ".paseo"
    home.mkdir()
    path = home / "config.json"
    path.write_text(json.dumps({"version": 1, "agents": {"providers": {"pi": {}}}}))
    path.chmod(0o600)
    original = path.read_bytes()
    monkeypatch.setenv("PASEO_HOME", str(home))
    monkeypatch.setattr(paseo_hermes.shutil, "which", lambda _: "/bin/tool")
    monkeypatch.setattr(
        paseo_hermes.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("must not launch CLI"),
    )
    assert paseo_hermes.main([]) == 0
    assert path.read_bytes() == original


def test_apply_validates_acp_and_preserves_providers(monkeypatch, tmp_path):
    home = tmp_path / ".paseo"
    home.mkdir()
    path = home / "config.json"
    original = {"version": 1, "agents": {"providers": {"pi": {"label": "mine"}}}}
    path.write_text(json.dumps(original))
    path.chmod(0o600)
    monkeypatch.setenv("PASEO_HOME", str(home))
    monkeypatch.setenv(paseo_hermes.GATE, "1")
    monkeypatch.setattr(
        paseo_hermes.shutil,
        "which",
        lambda name: f"/bin/{name}",
    )
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[1:] == ["acp", "--check"]:
            return Mock(returncode=0)
        expected = copy.deepcopy(original)
        expected["agents"]["providers"]["hermes"] = paseo_hermes.provider("/bin/hermes")
        target = Path(args[-1]) / "config.json"
        target.write_text(json.dumps(expected))
        return Mock(returncode=0)

    monkeypatch.setattr(paseo_hermes.subprocess, "run", fake_run)
    assert paseo_hermes.main(["--apply"]) == 0
    assert calls[0] == ["/bin/hermes", "acp", "--check"]
    assert [call[0:5] for call in calls[1:]] == [
        ["/bin/paseo", "daemon", "config", "set", "agents.providers.hermes"],
        ["/bin/paseo", "daemon", "config", "set", "agents.providers.hermes"],
    ]
    assert calls[1][-1] != str(home)
    assert calls[2][-1] == str(home)


def test_missing_approval_never_launches_cli(monkeypatch, tmp_path):
    home = tmp_path / ".paseo"
    home.mkdir()
    (home / "config.json").write_text("{}")
    (home / "config.json").chmod(0o600)
    monkeypatch.setenv("PASEO_HOME", str(home))
    monkeypatch.delenv(paseo_hermes.GATE, raising=False)
    monkeypatch.setattr(paseo_hermes.shutil, "which", lambda _: "/bin/tool")
    monkeypatch.setattr(
        paseo_hermes.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("unapproved execution"),
    )
    assert paseo_hermes.main(["--apply"]) == 1


def test_reject_duplicate_user_json_keys():
    with pytest.raises(ValueError, match="duplicate"):
        paseo_hermes.decode_config('{"agents":{},"agents":{"providers":{}}}')


def test_provider_keeps_stable_symlink_launcher():
    assert paseo_hermes.provider("/opt/homebrew/bin/hermes")["command"] == [
        "/opt/homebrew/bin/hermes",
        "acp",
    ]


def test_hermes_preflight_rejects_lossy_cli_without_live_mutation(
    monkeypatch, tmp_path
):
    home = tmp_path / ".paseo"
    home.mkdir()
    path = home / "config.json"
    original = {
        "version": 1,
        "agents": {"providers": {"pi": {"label": "user"}}},
        "user_setting": {"must_survive": True},
    }
    path.write_text(json.dumps(original), encoding="utf-8")
    path.chmod(0o600)
    before = path.read_bytes()
    monkeypatch.setenv("PASEO_HOME", str(home))
    monkeypatch.setenv(paseo_hermes.GATE, "1")
    monkeypatch.setattr(paseo_hermes.shutil, "which", lambda name: f"/bin/{name}")
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[1:] == ["acp", "--check"]:
            return Mock(returncode=0)
        staged = Path(args[-1]) / "config.json"
        staged.write_text(json.dumps({"agents": {"providers": {"hermes": PROVIDER}}}))
        return Mock(returncode=0)

    monkeypatch.setattr(paseo_hermes.subprocess, "run", fake_run)
    assert paseo_hermes.main(["--apply"]) == 1
    assert len(calls) == 2
    assert calls[1][-1] != str(home)
    assert path.read_bytes() == before


def test_hermes_explicit_dry_run_never_runs_external_commands(monkeypatch, tmp_path):
    home = tmp_path / ".paseo"
    home.mkdir()
    path = home / "config.json"
    path.write_text("{}")
    path.chmod(0o600)
    monkeypatch.setenv("PASEO_HOME", str(home))
    monkeypatch.setenv(paseo_hermes.GATE, "1")
    monkeypatch.setattr(paseo_hermes.shutil, "which", lambda _: "/bin/cli")
    monkeypatch.setattr(
        paseo_hermes.subprocess,
        "run",
        lambda *a, **k: pytest.fail("dry-run spawned a subprocess"),
    )
    assert paseo_hermes.main(["--apply", "--dry-run"]) == 0
    assert path.read_text() == "{}"

def test_hermes_preflight_comparison_refuses_json_type_coercion():
    assert paseo_hermes.same_config({"value": 1}, {"value": True}) is False
    assert paseo_hermes.same_config({"value": 1}, {"value": 1.0}) is False
    nested = {"outer": {"b": [1, True]}}
    assert paseo_hermes.same_config(nested, nested)
