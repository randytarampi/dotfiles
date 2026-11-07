"""Operator-only Paseo acceptance: source negative cases, no live daemon.

An authenticated configuration read cannot prove quiescence, listener reachability
or correct-password behaviour. Those remain owner-approved host checks.
"""

import copy
from pathlib import Path

import pytest

from paseo_security import audit
from paseo_metadata import metadata_changes

ROOT = Path(__file__).resolve().parents[3]
BCRYPT = "$2b$12$" + "b" * 53


def configured():
    return {
        "daemon": {
            "listen": "127.0.0.1:6767",
            "relay": {"enabled": False},
            "hostnames": ["paseo.localhost"],
            "auth": {"password": BCRYPT},
        },
        "features": {"webUi": {"enabled": True}},
    }


@pytest.fixture(autouse=True)
def clear_process_overrides(monkeypatch):
    for key in (
        "DOTFILES_PASEO_RELAY",
        "PASEO_LISTEN",
        "PASEO_RELAY_ENABLED",
        "PASEO_WEB_UI_ENABLED",
    ):
        monkeypatch.delenv(key, raising=False)


def test_read_only_audit_never_claims_daemon_auth_or_tls_verified():
    current = configured()
    before = copy.deepcopy(current)
    result = audit(current, "localhost", required_hosts=["paseo.localhost"])
    assert result["listener"] == "127.0.0.1:6767"
    assert result["daemon_runtime"] == "UNKNOWN"
    assert result["tls_and_websocket"] == "UNKNOWN"
    assert result["stored_password"] == "bcrypt-present"
    assert BCRYPT not in repr(result)
    assert current == before


@pytest.mark.parametrize("unsafe_listen", ("0.0.0.0:6767", "192.0.2.1:6767"))
def test_public_native_listener_refused_even_when_stored_bcrypt_exists(unsafe_listen):
    current = configured()
    current["daemon"]["listen"] = unsafe_listen
    with pytest.raises(ValueError, match="loopback"):
        audit(current, "public", required_hosts=["paseo.localhost"])


def test_declared_public_mode_requires_real_bcrypt():
    current = configured()
    current["daemon"]["auth"]["password"] = "plaintext"
    with pytest.raises(ValueError, match="password"):
        audit(current, "public", required_hosts=["paseo.localhost"])


@pytest.mark.parametrize(
    "providers",
    [[], [{"provider": "claude", "model": "remote"}], ["pi"], {"pi": "model"}],
)
def test_metadata_manual_list_does_not_replace_user_provider_choices(providers):
    current = {"agents": {"metadataGeneration": {"providers": providers}}}
    before = copy.deepcopy(current)
    with pytest.raises(ValueError, match="user-owned"):
        metadata_changes(current, "omlx/qualified-local-model")
    assert current == before


def test_metadata_selection_produces_one_explicit_provider_and_is_idempotent():
    current = {"agents": {"metadataGeneration": {}}}
    desired = [{"provider": "pi", "model": "omlx/qualified-local-model"}]
    assert metadata_changes(current, "omlx/qualified-local-model") == desired
    current["agents"]["metadataGeneration"]["providers"] = desired
    assert metadata_changes(current, "omlx/qualified-local-model") == []


def test_canonical_deploy_does_not_apply_native_paseo_settings():
    """Guard against accidental promotion of manual --apply to make deploy."""
    content = (ROOT / "scripts/configure-all.sh").read_text(encoding="utf-8")
    before = content.split("# 8a-1. Read-only Paseo Web UI inspection", 1)
    assert len(before) == 2
    lane = before[1].split("# 8b. Caddy config", 1)
    assert len(lane) == 2
    commands = [
        line for line in lane[0].splitlines() if "configure-paseo-web.py" in line
    ]
    assert len(commands) == 1
    assert "--apply" not in commands[0]
    assert "owner-only apply" in lane[0]
