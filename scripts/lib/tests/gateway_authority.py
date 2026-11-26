"""Synthetic, hermetic gateway authorisation for client-writer tests (#361).

Direct OpenCode and Pi generation refuse to emit gateway-only output unless the
same read-only contract ``configure-all.sh`` preflights holds: a current route
projection, a verified-model report that still authorises it, a live OAuth
cache, a fresh managed launch receipt and an advertisement set that matches the
plan. Tests must be able to say "this generation is current" without reading
the host's real ``~/.local/share/litellm``, reaching a service or holding a
credential.

Nothing here performs network, process or inference work: the managed-launch
receipt is stubbed and every fixture value is synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path

import litellm_route_preflight as preflight
import model_catalogues
from free_route_manifest import config_binding_digest

FIXTURE_MODEL_ID = "gpt-fixture"
FIXTURE_ALIAS = f"openai/{FIXTURE_MODEL_ID}"
# 2100-01-01: far past any test run, so the OAuth cache always looks live.
FIXTURE_EXPIRY = 4102444800

MISSING = "missing"
UNREADABLE = "unreadable"
FORGED = "forged"


def _rows(planned, wire=None):
    rows = []
    for alias in sorted(planned):
        model_id = alias.split("/", 1)[1]
        rows.append(
            {
                "model_name": alias,
                "litellm_params": {"model": (wire or "chatgpt") + "/" + model_id},
            }
        )
    return rows


def install_gateway_authority(
    home,
    monkeypatch,
    *,
    planned=None,
    served=None,
    wire=None,
    report_ids=None,
    report_state=None,
    oauth_expiry=FIXTURE_EXPIRY,
    receipt=None,
    projection="valid",
    gate="1",
):
    """Install one synthetic authorisation generation under ``home``.

    ``home`` must be the HOME the writer will run with. Returns the advertised
    alias set so a caller can hand the same evidence to a writer that reads the
    catalogue itself instead of repeating the request.
    """
    planned = {FIXTURE_ALIAS} if planned is None else set(planned)
    advertised = planned if served is None else set(served)

    state = Path(home) / ".local/share/litellm"
    clients = state / "clients"
    clients.mkdir(parents=True, exist_ok=True)
    clients.chmod(0o700)

    rows = _rows(planned, wire=wire)
    document = {
        "config_binding_sha256": config_binding_digest(rows),
        "routes": rows,
    }
    projection_file = state / "free-route-config.json"
    if projection == MISSING:
        projection_file.unlink(missing_ok=True)
    elif projection == UNREADABLE:
        projection_file.write_text("{not json", encoding="utf-8")
    else:
        if projection == FORGED:
            document["config_binding_sha256"] = "0" * 64
        projection_file.write_text(json.dumps(document), encoding="utf-8")

    report = state / "chatgpt_verified_models.json"
    if report_state == "missing":
        report.unlink(missing_ok=True)
    else:
        ids = (
            sorted({alias.split("/", 1)[1] for alias in planned})
            if report_ids is None
            else sorted(report_ids)
        )
        report.write_text(json.dumps(ids), encoding="utf-8")
        report.chmod(0o644 if report_state == "unsafe" else 0o600)

    oauth_dir = Path(home) / ".config/litellm/chatgpt"
    oauth_file = oauth_dir / "auth.json"
    if oauth_expiry is None:
        oauth_file.unlink(missing_ok=True)
    else:
        oauth_dir.mkdir(parents=True, exist_ok=True)
        oauth_file.write_text(
            json.dumps(
                {
                    "access_token": "fixture-access",
                    "refresh_token": "fixture-refresh",
                    "expires_at": oauth_expiry,
                }
            ),
            encoding="utf-8",
        )

    monkeypatch.setenv("DOTFILES_LITELLM_OAUTH_PROVIDERS", gate)
    # The managed launcher lives in a service venv no test may own; the receipt
    # is process evidence, covered independently by test_litellm_generation.
    monkeypatch.setattr(preflight, "run_generation_check", lambda *a, **k: receipt)
    # No test may reach a real listener: the local gateway is a live service.
    monkeypatch.setattr(
        model_catalogues, "get_models", lambda url, key, stats=None: set(advertised)
    )
    return advertised


def install_client_key(home, filename, value="fixture-client-key"):
    """Write one private synthetic client key and return its path."""
    clients = Path(home) / ".local/share/litellm/clients"
    clients.mkdir(parents=True, exist_ok=True)
    clients.chmod(0o700)
    key = clients / filename
    key.write_text(value, encoding="utf-8")
    key.chmod(0o600)
    return key
