"""Fail-closed, read-only Paseo Caddy/auth posture checks."""

from paseo_web_policy import (
    desired_relay_state,
    configured_hostnames,
    require_password,
    valid_daemon,
    web_ui_features,
)


def audit(config, access, *, caddy_enabled=True, required_hosts=()):
    """Never infer that a password reached an actual running daemon."""
    if access not in ("localhost", "lan", "public"):
        raise ValueError("invalid CADDY_ACCESS")
    if not caddy_enabled:
        raise ValueError("Caddy service disabled; cannot claim a Caddy route")
    if not isinstance(config, dict):
        raise ValueError("Paseo config must be a JSON object")
    daemon = valid_daemon(config)
    web_ui_features(config, require_enabled=True)
    if access != "localhost" or desired_relay_state():
        require_password(daemon)
    hosts = configured_hostnames(daemon)
    if not set(required_hosts).issubset(hosts):
        raise ValueError("required Caddy hostnames absent from Paseo allowlist")
    # No daemon process state, credential value or HTTP response is inferred here.
    return {
        "access": access,
        "listener": "127.0.0.1:6767",
        "relay": ("enabled (declared)" if desired_relay_state() else "disabled"),
        "stored_password": (
            "bcrypt-present" if valid_bcrypt(daemon) else "not-qualified"
        ),
        "web_ui": "enabled",
        "daemon_runtime": "UNKNOWN",
        "tls_and_websocket": "UNKNOWN",
    }


def valid_bcrypt(daemon):
    from paseo_web_policy import BCRYPT

    auth = daemon.get("auth", {})
    value = auth.get("password") if isinstance(auth, dict) else None
    return isinstance(value, str) and bool(BCRYPT.fullmatch(value))
