"""Paseo Caddy mode, authentication and hostname security regression tests."""

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "configure_caddy", ROOT / "configure-caddy.py"
)
assert SPEC and SPEC.loader
caddy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(caddy)

HASH = "$2b$12$" + "A" * 53
DOMAIN = "local.example.test"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for key in (
        "PASEO_LISTEN",
        "PASEO_RELAY_ENABLED",
        "DOTFILES_PASEO_RELAY",
        "PASEO_WEB_UI_ENABLED",
        "DOTFILES_RUN_PASEO_WEB_SETUP",
    ):
        monkeypatch.delenv(key, raising=False)
    # The paseo lane is deployed on real hosts; an inherited gate plus the live
    # config would leak machine state into these tests. Point PASEO_HOME at an
    # absent per-test path so any stray gate hits the graceful skip instead.
    monkeypatch.setenv("PASEO_HOME", str(tmp_path / ".paseo"))


def paseo_options(access_mode, *, https_port="443"):
    return caddy.PaseoSiteOptions(
        access_mode=access_mode,
        bind_ip="192.168.1.10",
        cert_fullchain="/cert",
        cert_key="/key",
        local_domain_tls_line="tls internal",
        https_port=https_port,
    )


def test_site_modes_have_correct_exposure_and_forward_all_paths():
    local = "\n".join(
        caddy.build_paseo_site_blocks([DOMAIN], paseo_options("localhost"))
    )
    assert "https://paseo.localhost {" in local.splitlines()
    assert "https://paseo.local.example.test {" in local.splitlines()
    assert "bind 127.0.0.1" in local
    assert "reverse_proxy 127.0.0.1:6767" in local
    assert "X-Forwarded-Proto" in local
    assert "basic_auth" not in local
    lan = "\n".join(caddy.build_paseo_site_blocks([DOMAIN], paseo_options("lan")))
    assert "bind 192.168.1.10" in lan
    assert "@not_lan not remote_ip private_ranges" in lan
    assert "https://paseo.local.example.test {" in lan.splitlines()
    assert "tls /cert /key" in lan
    public = "\n".join(caddy.build_paseo_site_blocks([DOMAIN], paseo_options("public")))
    assert "https://paseo.local.example.test {" in public.splitlines()
    assert "@not_lan" not in public
    public_port = "\n".join(
        caddy.build_paseo_site_blocks(
            [DOMAIN], paseo_options("public", https_port="8443")
        )
    )
    assert "https://paseo.local.example.test:8443 {" in public_port.splitlines()
    assert "@not_lan" not in public_port


def test_openwebui_sites_follow_caddy_access_mode(monkeypatch):
    monkeypatch.setenv("DOTFILES_RUN_OPENWEBUI_SETUP", "1")
    monkeypatch.setenv("OPENCODE_PASSWORD", "test-password")
    monkeypatch.delenv("DOTFILES_OPENWEBUI_PUBLIC", raising=False)
    for mode in ("localhost", "lan", "public"):
        caddyfile = caddy.build_caddyfile(
            [DOMAIN],
            mode,
            "",
            "192.168.1.10",
            "/cert",
            "/key",
            "tls internal",
            "4096",
            "/portal",
            "443",
        )
        chat_sites = re.findall(r"https://chat\.[^{]+\{(.*?)\n\}", caddyfile, re.DOTALL)
        if mode == "localhost":
            assert len(chat_sites) == 2
            assert all("@not_lan" not in block for block in chat_sites)
        else:
            assert len(chat_sites) == 1
            assert all(("@not_lan" in block) == (mode == "lan") for block in chat_sites)


def configured_home(tmp_path, monkeypatch, *, password=True):
    home = tmp_path / ".paseo"
    home.mkdir(mode=0o700)
    data = {
        "daemon": {
            "listen": "127.0.0.1:6767",
            "relay": {"enabled": False},
            "hostnames": ["paseo.localhost", f"paseo.{DOMAIN}"],
        },
        "features": {"webUi": {"enabled": True}},
    }
    if password:
        data["daemon"]["auth"] = {"password": HASH}
    path = home / "config.json"
    path.write_text(json.dumps(data))
    path.chmod(0o600)
    monkeypatch.setenv("PASEO_HOME", str(home))
    return path, data


def test_checked_config_allows_localhost_and_authenticated_lan(tmp_path, monkeypatch):
    configured_home(tmp_path, monkeypatch)
    assert caddy.validate_paseo_proxy_config("localhost", [DOMAIN]) is None
    assert caddy.validate_paseo_proxy_config("lan", [DOMAIN]) is None
    assert caddy.validate_paseo_proxy_config("public", [DOMAIN]) is None


def test_missing_auth_and_hostnames_are_refused(tmp_path, monkeypatch):
    path, data = configured_home(tmp_path, monkeypatch, password=False)
    with pytest.raises(ValueError, match="password"):
        caddy.validate_paseo_proxy_config("lan", [DOMAIN])
    data["daemon"]["hostnames"] = ["paseo.localhost"]
    data["daemon"]["auth"] = {"password": HASH}
    path.write_text(json.dumps(data))
    path.chmod(0o600)
    with pytest.raises(ValueError, match="allowlist"):
        caddy.validate_paseo_proxy_config("public", [DOMAIN])


def test_public_requires_native_password(tmp_path, monkeypatch):
    configured_home(tmp_path, monkeypatch, password=False)
    with pytest.raises(ValueError, match="password"):
        caddy.validate_paseo_proxy_config("public", [DOMAIN])


def test_build_caddyfile_skips_absent_paseo_config(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("PASEO_HOME", str(tmp_path / ".paseo"))
    monkeypatch.setenv("DOTFILES_RUN_PASEO_WEB_SETUP", "1")
    monkeypatch.setenv("OPENCODE_PASSWORD", "test-password")
    caddyfile = caddy.build_caddyfile(
        [DOMAIN],
        "localhost",
        "",
        "192.168.1.10",
        "/cert",
        "/key",
        "tls internal",
        "4096",
        "/portal",
        "443",
    )
    assert "Paseo config not found; skipping Paseo Web UI lane" in caplog.text
    assert "explicitly run `configure-paseo-web.py --apply`" in caplog.text
    assert "reverse_proxy 127.0.0.1:6767" not in caddyfile


@pytest.mark.parametrize(
    "path_and_value",
    (
        (("daemon", "listen"), "0.0.0.0:6767"),
        (("features", "webUi", "enabled"), False),
    ),
)
def test_invalid_native_state_refuses_proxy(tmp_path, monkeypatch, path_and_value):
    path, data = configured_home(tmp_path, monkeypatch)
    keys, value = path_and_value
    node = data
    for key in keys[:-1]:
        node = node[key]
    node[keys[-1]] = value
    path.write_text(json.dumps(data))
    path.chmod(0o600)
    with pytest.raises(ValueError):
        caddy.validate_paseo_proxy_config("lan", [DOMAIN])


def test_external_listener_override_refuses_proxy(tmp_path, monkeypatch):
    configured_home(tmp_path, monkeypatch)
    monkeypatch.setenv("PASEO_LISTEN", "0.0.0.0:6767")
    with pytest.raises(ValueError, match="PASEO_LISTEN"):
        caddy.validate_paseo_proxy_config("lan", [DOMAIN])


def test_native_config_symlink_is_refused(tmp_path, monkeypatch):
    path, _ = configured_home(tmp_path, monkeypatch)
    target = tmp_path / "backup.json"
    path.rename(target)
    path.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        caddy.validate_paseo_proxy_config("lan", [DOMAIN])


def test_hosts_entries_cover_paseo_domains():
    from caddy_domains import HostServices, build_hosts_entries

    localhost = build_hosts_entries(
        [DOMAIN], "localhost", HostServices(paseo_enabled=True)
    )
    assert any("paseo.localhost" in entry for entry in localhost)
    assert any(f"paseo.{DOMAIN}" in entry for entry in localhost)
    off = build_hosts_entries([DOMAIN], "lan")
    assert all("paseo." not in entry for entry in off)


@pytest.mark.parametrize("mode", ("localhost", "lan", "public"))
def test_caddy_publishes_declared_relay_and_refuses_drift(tmp_path, monkeypatch, mode):
    path, data = configured_home(tmp_path, monkeypatch)
    # Declared hosted-relay intent coexists with the Caddy route pre-apply.
    monkeypatch.setenv("DOTFILES_PASEO_RELAY", "1")
    caddy.validate_paseo_proxy_config(mode, [DOMAIN])
    # ... and after the native relay is reconciled on.
    data["daemon"]["relay"]["enabled"] = True
    path.write_text(json.dumps(data))
    path.chmod(0o600)
    caddy.validate_paseo_proxy_config(mode, [DOMAIN])
    # Undeclared enabled relay remains refused for publication.
    monkeypatch.setenv("DOTFILES_PASEO_RELAY", "0")
    with pytest.raises(ValueError, match="relay must be disabled"):
        caddy.validate_paseo_proxy_config(mode, [DOMAIN])
    # Process-level override stays fail-closed in every declared posture.
    monkeypatch.setenv("DOTFILES_PASEO_RELAY", "1")
    monkeypatch.setenv("PASEO_RELAY_ENABLED", "true")
    with pytest.raises(ValueError, match="PASEO_RELAY_ENABLED"):
        caddy.validate_paseo_proxy_config(mode, [DOMAIN])


def test_caddy_localhost_relay_requires_native_bcrypt(tmp_path, monkeypatch):
    path, data = configured_home(tmp_path, monkeypatch, password=False)
    monkeypatch.setenv("DOTFILES_PASEO_RELAY", "1")
    for native_relay in (False, True):
        data["daemon"]["relay"]["enabled"] = native_relay
        path.write_text(json.dumps(data))
        path.chmod(0o600)
        with pytest.raises(ValueError, match="password"):
            caddy.validate_paseo_proxy_config("localhost", [DOMAIN])
    # Caddy's local-only route is still allowed without password if relay is off.
    monkeypatch.setenv("DOTFILES_PASEO_RELAY", "0")
    data["daemon"]["relay"]["enabled"] = False
    path.write_text(json.dumps(data))
    path.chmod(0o600)
    assert caddy.validate_paseo_proxy_config("localhost", [DOMAIN]) is None


def test_caddy_refuses_unknown_relay_intent(tmp_path, monkeypatch):
    configured_home(tmp_path, monkeypatch)
    monkeypatch.setenv("DOTFILES_PASEO_RELAY", "typo")
    with pytest.raises(ValueError, match="DOTFILES_PASEO_RELAY"):
        caddy.validate_paseo_proxy_config("lan", [DOMAIN])
