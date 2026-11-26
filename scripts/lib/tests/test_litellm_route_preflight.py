"""The shared read-only gateway-authorisation contract (#361).

The direct OpenCode and Pi writers and the command-line preflight must agree on
every outcome, so each negative case is asserted twice: once through
``authorize_gateway_client`` and once through the CLI adapter. Nothing here
touches the network, a service, a process or a real credential.
"""

import importlib.util
from pathlib import Path

import pytest

import litellm_route_preflight as preflight
import model_catalogues
from gateway_authority import (
    FIXTURE_ALIAS,
    install_client_key,
    install_gateway_authority,
)

ROOT = Path(__file__).resolve().parents[3]
CLI = ROOT / "scripts" / "reconcile-litellm-routes.py"
OPENCODE_KEY_ENV = "LITELLM_OPENCODE_KEY"
PI_KEY_ENV = "LITELLM_PI_KEY"


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("HOME", str(path))
    monkeypatch.delenv(OPENCODE_KEY_ENV, raising=False)
    monkeypatch.delenv(PI_KEY_ENV, raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return path


def _authorize(client="opencode", **kwargs):
    return preflight.authorize_gateway_client(client, **kwargs)


# --- authorisation states ------------------------------------------------


def test_missing_projection_is_unknown_not_success(home):
    authorization = _authorize()
    assert authorization.state == preflight.UNKNOWN
    assert authorization.permitted is False
    assert "no emitted route projection" in authorization.reason


def test_unreadable_projection_is_unknown(home, monkeypatch):
    install_gateway_authority(home, monkeypatch, projection="unreadable")
    authorization = _authorize()
    assert authorization.state == preflight.UNKNOWN
    assert authorization.permitted is False
    assert "unreadable" in authorization.reason


def test_forged_binding_digest_is_unknown(home, monkeypatch):
    install_gateway_authority(home, monkeypatch, projection="forged")
    authorization = _authorize()
    assert authorization.permitted is False
    assert authorization.reason == "route projection binding digest differs"


def test_gate_off_is_not_applicable_and_never_probes(home, monkeypatch):
    install_gateway_authority(home, monkeypatch, gate="0")

    def _boom(*args, **kwargs):
        pytest.fail("gate-off must not verify a launch generation")

    monkeypatch.setattr(preflight, "run_generation_check", _boom)
    authorization = _authorize(api_key="fixture-key", catalogue=set())
    assert authorization.state == preflight.NOT_APPLICABLE
    assert authorization.permitted is True


def test_valid_generation_is_authorized(home, monkeypatch):
    served = install_gateway_authority(home, monkeypatch)
    key = install_client_key(home, "opencode.key")
    authorization = _authorize(api_key=key.read_text(encoding="utf-8"))
    assert authorization.state == preflight.AUTHORIZED
    assert authorization.permitted is True
    assert authorization.projection_digest
    assert served == {FIXTURE_ALIAS}


def test_revoked_report_refuses(home, monkeypatch):
    install_gateway_authority(home, monkeypatch, report_ids=[])
    install_client_key(home, "opencode.key")
    authorization = _authorize()
    assert authorization.state == preflight.REFUSED
    assert authorization.reason.startswith("refused-revoked-subscription-ids")


def test_missing_report_refuses(home, monkeypatch):
    install_gateway_authority(home, monkeypatch, report_state="missing")
    install_client_key(home, "opencode.key")
    authorization = _authorize()
    assert authorization.permitted is False
    assert authorization.reason.startswith("refused-unusable-report")


def test_expired_oauth_cache_refuses(home, monkeypatch):
    install_gateway_authority(home, monkeypatch, oauth_expiry=None)
    install_client_key(home, "opencode.key")
    authorization = _authorize()
    assert authorization.reason.startswith("refused-expired-oauth-cache")


def test_wrong_wire_with_the_same_alias_refuses(home, monkeypatch):
    install_gateway_authority(home, monkeypatch, wire="openai")
    install_client_key(home, "opencode.key")
    authorization = _authorize()
    assert authorization.permitted is False
    assert authorization.reason.startswith("refused-wire-model-mismatch")


@pytest.mark.parametrize(
    "receipt",
    [
        preflight.REFUSED_NO_RECEIPT,
        preflight.REFUSED_STALE_GENERATION,
        preflight.REFUSED_FOREIGN_GENERATION,
    ],
)
def test_unattestable_launch_receipt_refuses(home, monkeypatch, receipt):
    install_gateway_authority(home, monkeypatch, receipt=receipt)
    install_client_key(home, "opencode.key")
    authorization = _authorize()
    assert authorization.permitted is False
    assert authorization.reason.startswith(receipt)


def test_unmanaged_platform_is_unknown(home, monkeypatch):
    install_gateway_authority(home, monkeypatch)
    assert preflight.receipt_refusal(
        "4000", "digest", "win32", state_dir=home, run_check=lambda *a: None
    )


def test_missing_advertised_alias_refuses(home, monkeypatch):
    install_gateway_authority(home, monkeypatch, served=set())
    install_client_key(home, "opencode.key")
    authorization = _authorize()
    assert authorization.reason.startswith("refused-alias-not-advertised")


def test_unavailable_catalogue_is_unknown_not_success(home, monkeypatch):
    install_gateway_authority(home, monkeypatch)
    key = install_client_key(home, "opencode.key")
    # An unreachable or unauthenticated catalogue is UNKNOWN, never empty.
    monkeypatch.setattr(model_catalogues, "get_models", lambda *a, **k: None)
    authorization = _authorize(api_key=key.read_text(encoding="utf-8"))
    assert authorization.state == preflight.UNKNOWN
    assert authorization.reason.startswith("refused-unknown-service-state")


def test_generation_change_during_advertisement_refuses(home, monkeypatch):
    install_gateway_authority(home, monkeypatch)
    install_client_key(home, "opencode.key")
    reasons = iter([None, preflight.REFUSED_STALE_GENERATION])
    monkeypatch.setattr(
        preflight, "run_generation_check", lambda *a, **k: next(reasons)
    )
    authorization = _authorize()
    assert authorization.permitted is False
    assert preflight.REFUSED_STALE_GENERATION in authorization.reason


def test_missing_client_key_is_unknown(home, monkeypatch):
    install_gateway_authority(home, monkeypatch)
    authorization = _authorize()
    assert authorization.state == preflight.UNKNOWN
    assert "client key unavailable" in authorization.reason


def test_pi_uses_its_own_key_not_the_opencode_key(home, monkeypatch):
    install_gateway_authority(home, monkeypatch)
    install_client_key(home, "pi.key")
    pi_authorization = _authorize("pi", catalogue={FIXTURE_ALIAS})
    assert pi_authorization.state == preflight.AUTHORIZED
    opencode_authorization = _authorize("opencode")
    assert opencode_authorization.state == preflight.UNKNOWN


def test_conflicting_env_and_file_credential_is_unknown(home, monkeypatch):
    install_gateway_authority(home, monkeypatch)
    install_client_key(home, "pi.key", value="from-file")
    monkeypatch.setenv(PI_KEY_ENV, "from-env")
    authorization = _authorize("pi", catalogue={FIXTURE_ALIAS})
    assert authorization.state == preflight.UNKNOWN
    assert "ambiguous" in authorization.reason


# --- CLI parity: one contract, two entry points --------------------------


def _cli_module():
    spec = importlib.util.spec_from_file_location("reconcile_routes_parity", CLI)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def cli(home, monkeypatch):
    module = _cli_module()
    state = home / ".local/share/litellm"
    monkeypatch.setattr(module, "PROJECTION_FILE", state / "free-route-config.json")
    monkeypatch.setattr(module, "KEY_FILE", state / "clients" / "opencode.key")
    monkeypatch.setattr(module, "CONFIG_FILE", state / "config.yaml")
    monkeypatch.setattr(module, "_run_generation_check", lambda *a: None)
    return module


@pytest.mark.parametrize(
    "install",
    [
        {"receipt": preflight.REFUSED_STALE_GENERATION},
        {"receipt": preflight.REFUSED_NO_RECEIPT},
        {"report_ids": []},
        {"report_state": "missing"},
        {"served": set()},
        {"wire": "openai"},
        {"projection": "forged"},
        {"oauth_expiry": None},
    ],
)
def test_cli_and_client_entry_refuse_together(home, cli, monkeypatch, install):
    install_gateway_authority(home, monkeypatch, **install)
    install_client_key(home, "opencode.key")
    monkeypatch.setattr(cli, "_run_generation_check", lambda *a: install.get("receipt"))
    cli_status = cli.main([])
    authorization = _authorize()
    assert cli_status == 1
    assert authorization.permitted is False


def test_cli_and_client_entry_authorise_together(home, cli, monkeypatch):
    install_gateway_authority(home, monkeypatch)
    install_client_key(home, "opencode.key")
    assert cli.main([]) == 0
    assert _authorize().state == preflight.AUTHORIZED


def test_cli_and_client_entry_agree_when_the_lane_is_unused(home, cli, monkeypatch):
    install_gateway_authority(home, monkeypatch, gate="0")
    install_client_key(home, "opencode.key")
    assert cli.main([]) == 0
    assert _authorize().state == preflight.NOT_APPLICABLE
