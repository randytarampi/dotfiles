"""Paseo Caddy mode, authentication and hostname security regression tests."""

import importlib.util
import json
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
def isolated(monkeypatch):
    for key in ("PASEO_LISTEN", "PASEO_RELAY_ENABLED", "PASEO_WEB_UI_ENABLED"):
        monkeypatch.delenv(key, raising=False)


def test_site_modes_have_correct_exposure_and_forward_all_paths():
    args = (
        [DOMAIN],
        "localhost",
        "192.168.1.10",
        "/cert",
        "/key",
        "tls internal",
        "443",
    )
    local = "\n".join(caddy.build_paseo_site_blocks(*args, public_opt_in=False))
    assert "https://paseo.localhost {" in local.splitlines()
    assert "https://paseo.local.example.test {" in local.splitlines()
    assert "bind 127.0.0.1" in local
    assert "reverse_proxy 127.0.0.1:6767" in local
    assert "X-Forwarded-Proto" in local
    assert "basic_auth" not in local
    lan = "\n".join(
        caddy.build_paseo_site_blocks(
            [DOMAIN],
            "lan",
            "192.168.1.10",
            "/cert",
            "/key",
            "tls internal",
            "443",
            True,
        )
    )
    assert "bind 192.168.1.10" in lan
    assert "@not_lan not remote_ip private_ranges" in lan
    assert "https://paseo.local.example.test {" in lan.splitlines()
    assert "tls /cert /key" in lan
    public_private = "\n".join(
        caddy.build_paseo_site_blocks(
            [DOMAIN],
            "public",
            "192.168.1.10",
            "/cert",
            "/key",
            "tls internal",
            "443",
            False,
        )
    )
    assert "@not_lan" in public_private
    public_opted = "\n".join(
        caddy.build_paseo_site_blocks(
            [DOMAIN],
            "public",
            "192.168.1.10",
            "/cert",
            "/key",
            "tls internal",
            "8443",
            True,
        )
    )
    assert "https://paseo.local.example.test:8443 {" in public_opted.splitlines()
    assert "@not_lan" not in public_opted


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


@pytest.mark.parametrize(
    "path_and_value",
    (
        (("daemon", "listen"), "0.0.0.0:6767"),
        (("daemon", "relay", "enabled"), True),
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
    from caddy_domains import build_hosts_entries

    localhost = build_hosts_entries([DOMAIN], "localhost", paseo_enabled=True)
    assert any("paseo.localhost" in entry for entry in localhost)
    assert any(f"paseo.{DOMAIN}" in entry for entry in localhost)
    off = build_hosts_entries([DOMAIN], "lan", paseo_enabled=False)
    assert all("paseo." not in entry for entry in off)
