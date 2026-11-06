"""Adversarial checks for the Paseo Web UI Caddy deployment lane."""

import copy
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "configure-paseo-web.py"
SPEC = importlib.util.spec_from_file_location("configure_paseo_web", SCRIPT)
assert SPEC and SPEC.loader
web = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(web)

HASH = "$2b$12$" + "A" * 53
BASE_CONFIG = {
    "daemon": {
        "listen": "127.0.0.1:6767",
        "auth": {"password": HASH},
        "relay": {"enabled": False},
    }
}


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    for name in (
        "DOTFILES_RUN_PASEO_WEB_SETUP",
        "DOTFILES_RUN_CADDY_SETUP",
        "CADDY_ACCESS",
        "PASEO_LISTEN",
        "PASEO_RELAY_ENABLED",
        "DOTFILES_PASEO_RELAY",
        "PASEO_WEB_UI_ENABLED",
        "CADDY_ZONES_CONFIG",
    ):
        monkeypatch.delenv(name, raising=False)
    # The policy falls back to the machine's real ddns-zones.json when the
    # env is unset; pin every test to an absent per-test zones file.
    monkeypatch.setenv("CADDY_ZONES_CONFIG", str(tmp_path / "zones.json"))


def home_with_config(tmp_path, config=None):
    home = tmp_path / ".paseo"
    home.mkdir(mode=0o700)
    config_path = home / "config.json"
    config_path.write_text(json.dumps(config if config is not None else BASE_CONFIG))
    config_path.chmod(0o600)
    return home


def install_stub(
    monkeypatch, home, *, rewrite_unrelated=False, fail_live=False, fail_reload=False
):
    monkeypatch.setenv("PASEO_HOME", str(home))
    monkeypatch.setattr(web.shutil, "which", lambda name: "/bin/paseo")
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(list(argv))
        assert argv[0:2] == ["/bin/paseo", "daemon"]
        assert (
            argv[-2] == "--home"
        ), "native Paseo CLI must always select the inspected home"
        target = Path(argv[-1]) / "config.json"
        if fail_reload and target == home / "config.json" and argv[2] == "reload":
            return Mock(returncode=23, stderr="sensitive-native-output")
        if argv[2:4] == ["config", "set"]:
            if fail_live and target == home / "config.json":
                return Mock(returncode=1, stderr="refused")
            config = json.loads(target.read_text())
            keys = argv[4].split(".")
            current = config
            for key in keys[:-1]:
                current = current.setdefault(key, {})
            current[keys[-1]] = json.loads(argv[5])
            if rewrite_unrelated:
                config["unrelated"] = "unexpected"
            target.write_text(json.dumps(config))
            target.chmod(0o600)
        return Mock(returncode=0, stderr="")

    monkeypatch.setattr(web.subprocess, "run", fake_run)
    return calls


def test_gate_off_does_not_mutate(tmp_path, monkeypatch):
    home = home_with_config(tmp_path)
    install_stub(monkeypatch, home)
    before = (home / "config.json").read_bytes()
    assert web.main(["--apply"]) == 0
    assert (home / "config.json").read_bytes() == before


def test_apply_requires_paseo_cli(tmp_path, monkeypatch):
    home = home_with_config(tmp_path)
    monkeypatch.setenv("PASEO_HOME", str(home))
    monkeypatch.setenv(web.GATE, "1")
    monkeypatch.setattr(web.shutil, "which", lambda name: None)
    assert web.main(["--apply"]) == 1


def test_absent_config_warns_and_skips_without_writes(tmp_path, monkeypatch, capsys):
    home = tmp_path / ".paseo"
    monkeypatch.setenv("PASEO_HOME", str(home))
    monkeypatch.setenv(web.GATE, "1")
    monkeypatch.setattr(web.shutil, "which", lambda name: "/bin/paseo")
    assert web.main(["--apply"]) == 0
    output = capsys.readouterr().out
    assert "Paseo config not found; skipping Paseo Web UI lane" in output
    assert "explicitly run `configure-paseo-web.py --apply`" in output
    assert "next deploy configures it" not in output
    assert not home.exists()


def test_dry_run_and_unapplied_are_read_only(tmp_path, monkeypatch):
    home = home_with_config(tmp_path)
    calls = install_stub(monkeypatch, home)
    monkeypatch.setenv(web.GATE, "1")
    before = (home / "config.json").read_bytes()
    assert web.main(["--dry-run"]) == 0
    assert web.main([]) == 0
    assert not calls
    assert (home / "config.json").read_bytes() == before


def test_apply_uses_preflight_and_correct_home(tmp_path, monkeypatch):
    home = home_with_config(tmp_path)
    calls = install_stub(monkeypatch, home)
    monkeypatch.setenv(web.GATE, "1")
    assert web.main(["--apply"]) == 0
    assert [call[4] for call in calls if call[2:4] == ["config", "set"]] == [
        "features.webUi.enabled",
        "features.webUi.enabled",
    ]
    assert calls[-1][2] == "reload"
    assert calls[-1][-1] == str(home)
    actual = json.loads((home / "config.json").read_text())
    assert actual["features"]["webUi"]["enabled"] is True
    assert actual["daemon"] == BASE_CONFIG["daemon"]
    backup = home / "config.json.pre-web-ui"
    assert backup.exists()
    assert backup.stat().st_mode & 0o777 == 0o600


def test_hostname_reconciliation_and_idempotence(tmp_path, monkeypatch):
    home = home_with_config(tmp_path)
    calls = install_stub(monkeypatch, home)
    monkeypatch.setenv(web.GATE, "1")
    monkeypatch.setenv("DOTFILES_RUN_CADDY_SETUP", "1")
    monkeypatch.setenv("CADDY_ACCESS", "localhost")
    assert web.main(["--apply"]) == 0
    config = json.loads((home / "config.json").read_text())
    assert config["daemon"]["hostnames"] == ["paseo.localhost"]
    count = len(calls)
    assert web.main(["--apply"]) == 0
    assert len(calls) == count


def test_backup_preserved_and_no_backup_respected(tmp_path, monkeypatch):
    home = home_with_config(tmp_path)
    install_stub(monkeypatch, home)
    monkeypatch.setenv(web.GATE, "1")
    old_backup = home / "config.json.pre-web-ui"
    old_backup.write_bytes(b"keep first known-good snapshot")
    assert web.main(["--apply"]) == 0
    assert old_backup.read_bytes() == b"keep first known-good snapshot"
    home2 = tmp_path / "other"
    home2.mkdir(mode=0o700)
    (home2 / "config.json").write_text(json.dumps(BASE_CONFIG))
    (home2 / "config.json").chmod(0o600)
    install_stub(monkeypatch, home2)
    assert web.main(["--apply", "--no-backup"]) == 0
    assert not (home2 / "config.json.pre-web-ui").exists()


def test_listener_override_fails_closed_and_relay_drift_reconciles(
    tmp_path, monkeypatch
):
    home = home_with_config(tmp_path)
    install_stub(monkeypatch, home)
    monkeypatch.setenv(web.GATE, "1")
    path = home / "config.json"
    # Relay drift is NOT a safety boundary: the lane reconciles it back to
    # the declared DOTFILES_PASEO_RELAY posture (default: disabled) with a
    # warning, instead of failing the deploy on a non-conforming field.
    drifted = copy.deepcopy(BASE_CONFIG)
    drifted["daemon"]["relay"] = {"enabled": True}
    path.write_text(json.dumps(drifted))
    path.chmod(0o600)
    assert web.main(["--apply"]) == 0
    config = json.loads(path.read_text())
    assert config["daemon"]["relay"]["enabled"] is False
    # The listener is a safety boundary the lane never rewrites.
    candidate = copy.deepcopy(BASE_CONFIG)
    candidate["daemon"]["listen"] = "0.0.0.0:6767"
    path.write_text(json.dumps(candidate))
    path.chmod(0o600)
    assert web.main(["--apply"]) == 1
    monkeypatch.setenv("PASEO_LISTEN", "0.0.0.0:6767")
    assert web.main(["--apply"]) == 1


def test_lan_public_require_native_password_when_caddy_active(tmp_path, monkeypatch):
    candidate = copy.deepcopy(BASE_CONFIG)
    candidate["daemon"].pop("auth")
    home = home_with_config(tmp_path, candidate)
    install_stub(monkeypatch, home)
    monkeypatch.setenv(web.GATE, "1")
    monkeypatch.setenv("DOTFILES_RUN_CADDY_SETUP", "1")
    monkeypatch.setenv("CADDY_ACCESS", "public")
    assert web.main(["--apply"]) == 1


@pytest.mark.parametrize("caddy_gate", ("0", "1"))
def test_relay_apply_without_native_bcrypt_refuses_before_writes(
    tmp_path, monkeypatch, caddy_gate
):
    unprotected = copy.deepcopy(BASE_CONFIG)
    unprotected["daemon"].pop("auth")
    home = home_with_config(tmp_path, unprotected)
    calls = install_stub(monkeypatch, home)
    monkeypatch.setenv(web.GATE, "1")
    monkeypatch.setenv("DOTFILES_PASEO_RELAY", "1")
    monkeypatch.setenv("DOTFILES_RUN_CADDY_SETUP", caddy_gate)
    monkeypatch.setenv("CADDY_ACCESS", "localhost")
    before = (home / "config.json").read_bytes()
    assert web.main(["--apply"]) == 1
    assert not calls
    assert (home / "config.json").read_bytes() == before


def test_lossy_preflight_refuses_live_write_without_rollback(tmp_path, monkeypatch):
    home = home_with_config(tmp_path)
    calls = install_stub(monkeypatch, home, rewrite_unrelated=True)
    monkeypatch.setenv(web.GATE, "1")
    before = (home / "config.json").read_bytes()
    assert web.main(["--apply"]) == 1
    assert (home / "config.json").read_bytes() == before
    assert not (home / "config.json.pre-web-ui").exists()
    assert all(call[-1] != str(home) for call in calls)


def test_native_set_failure_does_not_restore_stale_backup(tmp_path, monkeypatch):
    home = home_with_config(tmp_path)
    install_stub(monkeypatch, home, fail_live=True)
    monkeypatch.setenv(web.GATE, "1")
    assert web.main(["--apply"]) == 1
    assert (home / "config.json.pre-web-ui").exists()


def test_saved_hosted_relay_reload_failure_is_a_partial_failure(
    tmp_path, monkeypatch, capsys
):
    home = home_with_config(tmp_path)
    calls = install_stub(monkeypatch, home, fail_reload=True)
    monkeypatch.setenv(web.GATE, "1")
    monkeypatch.setenv("DOTFILES_PASEO_RELAY", "1")
    assert web.main(["--apply"]) == 1
    config = json.loads((home / "config.json").read_text())
    assert config["daemon"]["relay"]["enabled"] is True
    assert config["features"]["webUi"]["enabled"] is True
    assert (home / "config.json.pre-web-ui").exists()
    assert any(call[2] == "reload" and call[-1] == str(home) for call in calls)
    stderr = capsys.readouterr().err
    assert "config saved; daemon reload failed (exit 23)" in stderr
    assert "no rollback attempted" in stderr
    assert "sensitive-native-output" not in stderr


def test_deploy_cannot_write_paseo_native_settings_unattended():
    """Prevent make deploy from turning a Web UI gate into an unsafe CLI write."""
    orchestrator = (SCRIPT.parent / "configure-all.sh").read_text(encoding="utf-8")
    lane = orchestrator.split("# 8a-1. Read-only Paseo Web UI inspection", 1)
    assert len(lane) == 2
    section = lane[1].split("# 8b. Caddy config", 1)
    assert len(section) == 2
    assert 'python3 "$SCRIPT_DIR/configure-paseo-web.py"' in section[0]
    assert "--apply" not in section[0]

    makefile = (SCRIPT.parent.parent / "Makefile").read_text(encoding="utf-8")
    assert "paseo-web-check:" in makefile
    assert "paseo-web-apply:" in makefile
    apply_recipe = makefile.split("paseo-web-apply:", 1)[1].split("\n\n", 1)[0]
    assert "configure-paseo-web.py --apply" in apply_recipe


def test_abbreviated_apply_is_rejected():
    with pytest.raises(SystemExit) as error:
        web.main(["--app"])
    assert error.value.code == 2


def test_public_domain_hostname_is_only_added_by_explicit_setup(tmp_path, monkeypatch):
    home = home_with_config(tmp_path)
    install_stub(monkeypatch, home)
    zones = tmp_path / "ddns-zones.json"
    zones.write_text(
        json.dumps({"zones": [{"records": [{"name": "local.example.test"}]}]})
    )
    monkeypatch.setenv("CADDY_ZONES_CONFIG", str(zones))
    monkeypatch.setenv(web.GATE, "1")
    monkeypatch.setenv("DOTFILES_RUN_CADDY_SETUP", "1")
    monkeypatch.setenv("CADDY_ACCESS", "lan")
    assert web.main(["--apply"]) == 0
    config = json.loads((home / "config.json").read_text())
    assert config["daemon"]["hostnames"] == ["paseo.local.example.test"]


def test_relay_intent_must_be_boolean_before_native_write(tmp_path, monkeypatch):
    home = home_with_config(tmp_path)
    calls = install_stub(monkeypatch, home)
    monkeypatch.setenv(web.GATE, "1")
    monkeypatch.setenv("DOTFILES_PASEO_RELAY", "enabled-ish")
    original = (home / "config.json").read_bytes()
    assert web.main(["--apply"]) == 1
    assert not calls
    assert (home / "config.json").read_bytes() == original


def test_caddy_gate_reconciles_declared_relay_during_owner_apply(
    tmp_path, monkeypatch, capsys
):
    home = home_with_config(tmp_path)
    calls = install_stub(monkeypatch, home)
    zones = tmp_path / "ddns-zones.json"
    zones.write_text(
        json.dumps({"zones": [{"records": [{"name": "local.example.test"}]}]})
    )
    monkeypatch.setenv("CADDY_ZONES_CONFIG", str(zones))
    monkeypatch.setenv(web.GATE, "1")
    monkeypatch.setenv("DOTFILES_RUN_CADDY_SETUP", "1")
    monkeypatch.setenv("CADDY_ACCESS", "lan")
    monkeypatch.setenv("DOTFILES_PASEO_RELAY", "1")
    assert web.main(["--apply"]) == 0
    assert calls  # reconciliation went through Paseo's own CLI writer
    config = json.loads((home / "config.json").read_text())
    assert config["daemon"]["relay"]["enabled"] is True
    assert "relay" in capsys.readouterr().out


@pytest.mark.parametrize("relay_config", ({"enabled": True}, {}))
def test_native_relay_drift_exception_is_not_shared_with_other_consumers(
    monkeypatch, relay_config
):
    from paseo_web_policy import valid_daemon, validate_native_config

    monkeypatch.delenv("PASEO_RELAY_ENABLED", raising=False)
    config = {"daemon": {"relay": relay_config}, "features": {"webUi": {}}}
    with pytest.raises(ValueError, match="relay must be disabled"):
        valid_daemon(config)
    assert validate_native_config(config, "localhost") == []
