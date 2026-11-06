"""Read-only Paseo security assessment checks."""

import copy

import pytest

from paseo_security import audit

BCRYPT = "$2b$12$" + "a" * 53


@pytest.fixture(autouse=True)
def isolated_relay_environment(monkeypatch):
    for key in (
        "DOTFILES_PASEO_RELAY",
        "PASEO_RELAY_ENABLED",
        "PASEO_LISTEN",
        "PASEO_WEB_UI_ENABLED",
    ):
        monkeypatch.delenv(key, raising=False)


def base():
    return {
        "daemon": {
            "listen": "127.0.0.1:6767",
            "relay": {"enabled": False},
            "hostnames": ["paseo.localhost", "paseo.example.test"],
            "auth": {"password": BCRYPT},
        },
        "features": {"webUi": {"enabled": True}},
    }


def test_lan_requires_password_and_loopback(monkeypatch):
    monkeypatch.delenv("PASEO_LISTEN", raising=False)
    monkeypatch.delenv("PASEO_RELAY_ENABLED", raising=False)
    assert (
        audit(base(), "lan", required_hosts=["paseo.example.test"])["stored_password"]
        == "bcrypt-present"
    )
    without = base()
    del without["daemon"]["auth"]
    with pytest.raises(ValueError, match="password"):
        audit(without, "lan")
    wrong = base()
    wrong["daemon"]["listen"] = "0.0.0.0:6767"
    with pytest.raises(ValueError, match="loopback"):
        audit(wrong, "lan")


def test_public_follows_caddy_access_but_still_requires_native_password():
    assert audit(base(), "public")["access"] == "public"
    unprotected = base()
    del unprotected["daemon"]["auth"]
    with pytest.raises(ValueError, match="password"):
        audit(unprotected, "public")


def test_disabled_caddy_and_relay_refused():
    with pytest.raises(ValueError, match="Caddy service"):
        audit(base(), "lan", caddy_enabled=False)
    wrong = base()
    wrong["daemon"]["relay"]["enabled"] = True
    with pytest.raises(ValueError, match="relay"):
        audit(wrong, "lan")


def test_missing_hostname_and_web_ui():
    with pytest.raises(ValueError, match="allowlist"):
        audit(base(), "lan", required_hosts=["paseo.unlisted.test"])
    wrong = base()
    wrong["features"]["webUi"]["enabled"] = False
    with pytest.raises(ValueError, match="not enabled"):
        audit(wrong, "lan")


def test_security_cli_requires_actual_lan_hostnames(monkeypatch, capsys):
    import importlib.util
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / "check-paseo-security.py"
    spec = importlib.util.spec_from_file_location("check_paseo_security_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setenv("CADDY_ACCESS", "lan")
    monkeypatch.setenv("DOTFILES_RUN_CADDY_SETUP", "1")
    monkeypatch.setattr(module, "load_env", lambda: True)
    monkeypatch.setattr(module, "read_proxy_config", base)
    monkeypatch.setattr(
        module, "domains_for_access", lambda access: ["paseo.required.test"]
    )
    assert module.main(["--json"]) == 1
    assert "hostname" in capsys.readouterr().err

    data = base()
    data["daemon"]["hostnames"].append("paseo.required.test")
    monkeypatch.setattr(module, "read_proxy_config", lambda: data)
    assert module.main(["--json"]) == 0
    assert '"access": "lan"' in capsys.readouterr().out


@pytest.mark.parametrize("intent", ("1", "true", "yes", "on"))
def test_audit_reports_declared_hosted_relay_with_caddy_route(monkeypatch, intent):
    """Owner-declared relay intent coexists with the Caddy route by declaration."""
    monkeypatch.setenv("DOTFILES_PASEO_RELAY", intent)
    config = base()
    before = copy.deepcopy(config)
    result = audit(config, "lan", required_hosts=["paseo.example.test"])
    assert result["relay"] == "enabled (declared)"
    assert config == before


def test_audit_accepts_declared_relay_with_native_enabled(monkeypatch):
    monkeypatch.setenv("DOTFILES_PASEO_RELAY", "1")
    config = base()
    config["daemon"]["relay"]["enabled"] = True
    result = audit(config, "lan", required_hosts=["paseo.example.test"])
    assert result["relay"] == "enabled (declared)"


@pytest.mark.parametrize("intent", ("", "0", "false", "no", "off"))
def test_audit_reports_disabled_relay_without_declared_intent(monkeypatch, intent):
    if intent:
        monkeypatch.setenv("DOTFILES_PASEO_RELAY", intent)
    result = audit(base(), "lan", required_hosts=["paseo.example.test"])
    assert result["relay"] == "disabled"


def test_audit_native_relay_without_declared_intent_still_refused(monkeypatch):
    monkeypatch.setenv("DOTFILES_PASEO_RELAY", "0")
    wrong = base()
    wrong["daemon"]["relay"]["enabled"] = True
    with pytest.raises(ValueError, match="relay must be disabled"):
        audit(wrong, "lan")


@pytest.mark.parametrize("intent", ("unknown", "typo", "2"))
def test_audit_refuses_unknown_relay_intent(monkeypatch, intent):
    monkeypatch.setenv("DOTFILES_PASEO_RELAY", intent)
    with pytest.raises(ValueError, match="DOTFILES_PASEO_RELAY"):
        audit(base(), "lan", required_hosts=["paseo.example.test"])


def test_security_cli_uses_canonical_env_and_local_zone_aliases(
    monkeypatch, tmp_path, capsys
):
    """Generated Caddy localhost aliases must exist in the native allowlist."""
    import importlib.util
    import json
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / "check-paseo-security.py"
    spec = importlib.util.spec_from_file_location("paseo_security_env_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    home = tmp_path / ".paseo"
    home.mkdir(mode=0o700)
    config = home / "config.json"
    state = base()
    config.write_text(json.dumps(state), encoding="utf-8")
    config.chmod(0o600)
    zones = tmp_path / "ddns-zones.json"
    zones.write_text(
        json.dumps({"zones": [{"records": [{"name": "local.example.test"}]}]})
    )
    (tmp_path / ".env").write_text(
        "CADDY_ACCESS=localhost\n"
        "DOTFILES_RUN_CADDY_SETUP=1\n"
        f"CADDY_ZONES_CONFIG={zones}\n"
        f"PASEO_HOME={home}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    for key in (
        "CADDY_ACCESS",
        "DOTFILES_RUN_CADDY_SETUP",
        "CADDY_ZONES_CONFIG",
        "PASEO_HOME",
    ):
        monkeypatch.delenv(key, raising=False)
    before = config.read_bytes()
    assert module.main(["--json"]) == 1
    assert "hostname" in capsys.readouterr().err
    assert config.read_bytes() == before

    state["daemon"]["hostnames"].append("paseo.local.example.test")
    config.write_text(json.dumps(state), encoding="utf-8")
    qualified = config.read_bytes()
    assert module.main(["--json"]) == 0
    output = capsys.readouterr().out
    assert '"access": "localhost"' in output
    assert config.read_bytes() == qualified


def test_security_cli_refuses_missing_canonical_env(monkeypatch, tmp_path, capsys):
    import importlib.util
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / "check-paseo-security.py"
    spec = importlib.util.spec_from_file_location("paseo_security_no_env_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert module.main(["--json"]) == 1
    assert "canonical ~/.env unavailable" in capsys.readouterr().err
