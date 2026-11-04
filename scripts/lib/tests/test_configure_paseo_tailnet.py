import copy
import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "configure-paseo-tailnet.py"
SPEC = importlib.util.spec_from_file_location("configure_paseo_tailnet", SCRIPT)
assert SPEC and SPEC.loader
paseo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(paseo)

HASH = "$2b$12$" + "A" * 53


def config(listener="127.0.0.1:6767"):
    return {
        "version": 1,
        "daemon": {
            "listen": listener,
            "auth": {"password": HASH},
            "relay": {"enabled": False},
        },
        "agents": {"providers": {"custom": {"label": "mine"}}},
    }


def test_configuration_and_custom_listener():
    desired = "100.101.102.103:6767"
    assert paseo.validate_config(config(), desired) == "change"
    assert paseo.validate_config(config(desired), desired) == "unchanged"
    with pytest.raises(ValueError):
        paseo.validate_config(config("0.0.0.0:6767"), desired)


@pytest.mark.parametrize(
    "alter",
    [
        lambda x: x["daemon"].pop("relay"),
        lambda x: x["daemon"]["relay"].update(enabled=True),
        lambda x: x["daemon"]["relay"].update(enabled="false"),
        lambda x: x["daemon"].pop("auth"),
        lambda x: x["daemon"]["auth"].update(password="plaintext"),
        lambda x: x.update(daemon=[]),
    ],
)
def test_reject_missing_relay_auth_and_malformed(alter):
    data = config()
    alter(data)
    with pytest.raises(ValueError):
        paseo.validate_config(data, "100.101.102.103:6767")


def test_tailnet_address_validation():
    assert paseo.tailnet_ip("100.101.102.103") == "100.101.102.103"
    for address in (
        "192.168.1.10",
        "127.0.0.1",
        "not-an-address",
        "100.101.102.103:9999",
    ):
        with pytest.raises(ValueError):
            paseo.tailnet_ip(address)


def test_read_only_does_not_change_config(monkeypatch, tmp_path):
    home = tmp_path / ".paseo"
    home.mkdir()
    path = home / "config.json"
    import json

    path.write_text(json.dumps(config()), encoding="utf-8")
    path.chmod(0o600)
    original = path.read_bytes()
    monkeypatch.setenv("PASEO_HOME", str(home))
    monkeypatch.setattr(paseo.shutil, "which", lambda _: "/bin/tool")
    run = Mock()
    run.return_value.stdout = "100.101.102.103\n"
    monkeypatch.setattr(paseo.subprocess, "run", run)
    assert paseo.main([]) == 0
    assert path.read_bytes() == original
    assert run.call_count == 1


def test_apply_preserves_unrelated_user_state(monkeypatch, tmp_path):
    import json

    home = tmp_path / ".paseo"
    home.mkdir()
    path = home / "config.json"
    data = config()
    path.write_text(json.dumps(data), encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.setenv("PASEO_HOME", str(home))
    monkeypatch.setenv(paseo.GATE, "1")
    monkeypatch.delenv("PASEO_LISTEN", raising=False)
    monkeypatch.delenv("PASEO_RELAY_ENABLED", raising=False)
    monkeypatch.setattr(paseo.shutil, "which", lambda _: "/bin/tool")
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[1:] == ["ip", "-4"]:
            return Mock(stdout="100.101.102.103\n")
        saved = copy.deepcopy(data)
        saved["daemon"]["listen"] = "100.101.102.103:6767"
        target = Path(args[-1]) / "config.json"
        target.write_text(json.dumps(saved), encoding="utf-8")
        return Mock(returncode=0)

    monkeypatch.setattr(paseo.subprocess, "run", fake_run)
    assert paseo.main(["--apply"]) == 0
    assert len(calls) == 3
    assert json.loads(path.read_text())["agents"] == data["agents"]


def test_override_prevents_mutation(monkeypatch, tmp_path):
    import json

    home = tmp_path / ".paseo"
    home.mkdir()
    (home / "config.json").write_text(json.dumps(config()))
    (home / "config.json").chmod(0o600)
    monkeypatch.setenv("PASEO_HOME", str(home))
    monkeypatch.setenv(paseo.GATE, "1")
    monkeypatch.setenv("PASEO_RELAY_ENABLED", "true")
    monkeypatch.setattr(paseo.shutil, "which", lambda _: "/bin/tool")
    run = Mock()
    run.return_value.stdout = "100.101.102.103\n"
    monkeypatch.setattr(paseo.subprocess, "run", run)
    assert paseo.main(["--apply"]) == 1
    assert run.call_count == 1


def test_reject_duplicate_user_json_keys():
    with pytest.raises(ValueError, match="duplicate"):
        paseo.decode_config('{"agents":{},"agents":{"providers":{}}}')


def test_reject_group_readable_config(tmp_path, monkeypatch):
    monkeypatch.setattr(
        paseo.os, "getuid", lambda: (tmp_path / "config.json").stat().st_uid
    )
    home = tmp_path
    (home / "config.json").write_text("{}")
    (home / "config.json").chmod(0o644)
    with pytest.raises(ValueError, match="exclusively owned"):
        paseo.checked_config(home)


def test_tailnet_explicit_dry_run_does_not_run_paseo(monkeypatch, tmp_path):
    import json

    home = tmp_path / ".paseo"
    home.mkdir()
    path = home / "config.json"
    path.write_text(json.dumps(config()), encoding="utf-8")
    path.chmod(0o600)
    before = path.read_bytes()
    monkeypatch.setenv("PASEO_HOME", str(home))
    monkeypatch.setenv(paseo.GATE, "1")
    monkeypatch.setattr(paseo.shutil, "which", lambda name: f"/bin/{name}")

    def fake_run(args, **kwargs):
        assert args[1:] == ["ip", "-4"]
        return Mock(stdout="100.101.102.103\n")

    monkeypatch.setattr(paseo.subprocess, "run", fake_run)
    assert paseo.main(["--apply", "--dry-run"]) == 0
    assert path.read_bytes() == before


def test_tailnet_preflight_comparison_refuses_json_type_coercion():
    assert paseo.same_config({"value": 1}, {"value": True}) is False
    assert paseo.same_config({"value": 1}, {"value": 1.0}) is False
    nested = {"outer": {"b": [1, True]}}
    assert paseo.same_config(nested, nested)


def test_abbreviated_apply_flag_rejected_before_mutation():
    with pytest.raises(SystemExit) as caught:
        paseo.main(["--app"])
    assert caught.value.code == 2
