"""Paseo Web UI policy shared by native config writes and Caddy routing.

The CLI may write only to an existing private home. The daemon remains on
loopback; Caddy owns TLS and network exposure. Never enable a relay here.
"""

import json
import os
from pathlib import Path
import re
import stat

from caddy_domains import load_domains

ACCESS_MODES = {"localhost", "lan", "public"}
DEFAULT_LISTEN = "127.0.0.1:6767"
WEB_UI_KEY = "features.webUi.enabled"
BCRYPT = re.compile(r"\$2[aby]\$\d\d\$[./A-Za-z0-9]{53}\Z")


class PaseoNotConfigured(RuntimeError):
    """Paseo home or daemon configuration has not been created yet."""


def valid_daemon(config):
    """Fail closed on listener, relay and launch overrides."""
    daemon = config.get("daemon", {})
    if not isinstance(daemon, dict):
        raise ValueError("Paseo daemon config must be an object")
    if os.environ.get("PASEO_LISTEN"):
        raise ValueError("PASEO_LISTEN would override the managed loopback listener")
    if daemon.get("listen", DEFAULT_LISTEN) != DEFAULT_LISTEN:
        raise ValueError("Paseo Caddy mode requires an unmodified loopback listener")
    relay = daemon.get("relay")
    if not isinstance(relay, dict) or relay.get("enabled") is not False:
        raise ValueError("Paseo relay must be explicitly disabled")
    if os.environ.get("PASEO_RELAY_ENABLED", "").lower() not in ("", "false", "0"):
        raise ValueError("PASEO_RELAY_ENABLED could override relay isolation")
    return daemon


def require_password(daemon):
    auth = daemon.get("auth")
    password = auth.get("password") if isinstance(auth, dict) else None
    if not isinstance(password, str) or not BCRYPT.fullmatch(password):
        raise ValueError("Caddy LAN/public access requires a Paseo daemon password")


def web_ui_features(config, *, require_enabled):
    features = config.get("features", {})
    if not isinstance(features, dict) or not isinstance(
        features.get("webUi", {}), dict
    ):
        raise ValueError("Paseo features.webUi must be an object")
    if os.environ.get("PASEO_WEB_UI_ENABLED", "").lower() not in ("", "true", "1"):
        raise ValueError("PASEO_WEB_UI_ENABLED contradicts the managed Web UI")
    if require_enabled and features.get("webUi", {}).get("enabled") is not True:
        raise ValueError("Paseo Web UI is not enabled in the native daemon config")
    return features


def configured_hostnames(daemon):
    hostnames = daemon.get("hostnames", [])
    if not isinstance(hostnames, list) or any(
        not isinstance(s, str) for s in hostnames
    ):
        raise ValueError("Paseo daemon.hostnames must be an array of strings")
    return hostnames


def desired_hostnames(access, domains):
    if access == "localhost":
        return ["paseo.localhost"] + [
            f"paseo.{domain}" for domain in domains if domain.startswith("local.")
        ]
    return [f"paseo.{domain}" for domain in domains]


def domains_for_access(access):
    if os.environ.get("DOTFILES_RUN_CADDY_SETUP", "0") != "1":
        return []
    zones = Path(
        os.path.expandvars(
            os.path.expanduser(
                os.environ.get("CADDY_ZONES_CONFIG", "~/.config/caddy/ddns-zones.json")
            )
        )
    )
    domains = load_domains(zones)
    if access != "localhost" and not domains:
        raise ValueError("Caddy LAN/public access requires configured domains")
    return desired_hostnames(access, domains)


def validate_native_config(config, access):
    if access not in ACCESS_MODES:
        raise ValueError("invalid CADDY_ACCESS; choose localhost, lan or public")
    daemon = valid_daemon(config)
    if access != "localhost" and os.environ.get("DOTFILES_RUN_CADDY_SETUP") == "1":
        require_password(daemon)
    web_ui_features(config, require_enabled=False)
    return configured_hostnames(daemon)


def _unique_keys(pairs):
    values = {}
    for key, value in pairs:
        if key in values:
            raise ValueError("duplicate Paseo configuration key")
        values[key] = value
    return values


def read_proxy_config():
    home = Path(os.environ.get("PASEO_HOME", str(Path.home() / ".paseo"))).expanduser()
    path = home / "config.json"
    if home.is_symlink() or home.parent.is_symlink() or path.is_symlink():
        raise ValueError("unsafe Paseo config symlink")
    if not home.exists() or not path.exists():
        raise PaseoNotConfigured("Paseo config not found")
    info = path.stat()
    if (
        info.st_uid != os.getuid()
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise ValueError("Paseo config must be a private regular file")
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle, object_pairs_hook=_unique_keys)
    if not isinstance(data, dict):
        raise ValueError("Paseo configuration must be an object")
    return data


def validate_proxy_config(access, domains):
    """Require safe persisted state before creating any Paseo Caddy route."""
    if access not in ACCESS_MODES:
        raise ValueError("invalid CADDY_ACCESS")
    data = read_proxy_config()
    daemon = valid_daemon(data)
    web_ui_features(data, require_enabled=True)
    if access != "localhost":
        require_password(daemon)
    required_hosts = set(desired_hostnames(access, domains))
    if not required_hosts.issubset(configured_hostnames(daemon)):
        raise ValueError(
            "Paseo hostname allowlist is missing generated Caddy hostnames"
        )
