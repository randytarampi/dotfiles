#!/usr/bin/env python3
"""Read-only preflight: does the gateway advertise the routes the plan emitted?

Exits 0 when every planned ``openai/<id>`` alias is bound to exactly
``chatgpt/<id>`` in the generator's own emitted projection and is advertised by
the running gateway to the client key. Exits 1 otherwise, naming the reason.

It never regenerates, never restarts, never writes client state and never calls
an admin endpoint. ``/v1/models`` advertises aliases and does not attest backend
identity, so presence is all that is established at runtime; wire identity comes
from the projection that ``write_config`` co-writes with the config it
describes. See #361.
"""

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

LIB_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from constants import get_litellm_oauth_gate  # noqa: E402 -- scripts/lib bootstrap.
from litellm_config import (  # noqa: E402 -- scripts/lib bootstrap.
    CHATGPT_REPORT_OK,
    chatgpt_verified_openai_models_report,
    oauth_cache_expiry,
)
from litellm_route_guard import (  # noqa: E402 -- scripts/lib bootstrap.
    REFUSED_AMBIGUOUS_CREDENTIALS,
    REFUSED_EXPIRED_AUTH,
    REFUSED_REVOKED,
    REFUSED_UNUSABLE_REPORT,
    alias_model_id,
    assert_namespace_matches,
    planned_subscription_bindings,
)

LITELLM_DIR = Path("~/.local/share/litellm").expanduser()
PROJECTION_FILE = LITELLM_DIR / "free-route-config.json"
KEY_FILE = LITELLM_DIR / "clients" / "opencode.key"
CONFIG_FILE = LITELLM_DIR / "config.yaml"
RECEIPT_FILE = LITELLM_DIR / ".loaded-generation"

REFUSED_NO_RECEIPT = "refused-no-loaded-generation-receipt"
REFUSED_STALE_GENERATION = "refused-stale-loaded-generation"
REFUSED_FOREIGN_GENERATION = "refused-loaded-generation-port-mismatch"


def _fail(reason):
    print(reason)
    return 1


def _load_plan():
    """Return ``((routes, aliases, planned_ids), None)`` or ``(None, reason)``."""
    try:
        projection = json.loads(PROJECTION_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, (
            "no emitted route projection; LiteLLM has not generated a config yet "
            "— run 'make deploy'"
        )
    except (OSError, ValueError) as error:
        return None, f"route projection is unreadable ({error})"
    routes = projection.get("routes")
    if not isinstance(routes, list):
        return None, "route projection has no route list"
    aliases = [
        row.get("model_name")
        for row in routes
        if isinstance(row, dict) and alias_model_id(row.get("model_name"))
    ]
    planned_ids = {alias_model_id(alias) for alias in aliases}
    return (routes, aliases, planned_ids), None


def _plan_refusal(routes, planned_ids):
    """Return a reason when the plan is not authorised by the current report."""
    if (os.environ.get("OPENAI_API_KEY") or "").strip() and planned_ids:
        return (
            f"{REFUSED_AMBIGUOUS_CREDENTIALS}: subscription aliases are planned "
            "while a direct paid OPENAI_API_KEY is set — unset the key or "
            "disable the OAuth gate"
        )
    if not planned_ids:
        return None
    if oauth_cache_expiry("chatgpt", os.environ) is None:
        # Generation would not emit these routes without a live token, so a
        # plan that still names them is stale authorisation.
        return (
            f"{REFUSED_EXPIRED_AUTH}: chatgpt OAuth cache is absent or expired "
            "while subscription aliases are planned — run "
            "scripts/litellm-oauth.py --provider chatgpt"
        )
    # A projection that outlives the authorisation it was built from must not
    # authorise a client.
    state, verified = chatgpt_verified_openai_models_report(os.environ)
    if state != CHATGPT_REPORT_OK:
        return (
            f"{REFUSED_UNUSABLE_REPORT}: verified-model report is {state} "
            "while subscription aliases are planned"
        )
    revoked = sorted(planned_ids - set(verified))
    if revoked:
        return f"{REFUSED_REVOKED}:{','.join(revoked[:3])}"
    newer = sorted(set(verified) - planned_ids)
    if newer:
        print(
            "warning: report verifies ids absent from the projection "
            f"({','.join(newer[:3])}); run 'make deploy' to regenerate"
        )
    wires_ok, wires_reason = planned_subscription_bindings(routes)
    return None if wires_ok else wires_reason


def _served_refusal(aliases, base_url):
    """Return a reason when the gateway does not advertise the plan."""
    try:
        api_key = KEY_FILE.read_text(encoding="utf-8").strip()
    except OSError as error:
        return f"cannot read gateway client key ({error})"
    if not api_key:
        return "gateway client key is empty"
    from model_catalogues import get_models  # noqa: E402 -- scripts/lib bootstrap.

    matches, matches_reason = assert_namespace_matches(
        aliases, get_models(f"{base_url}/models", api_key)
    )
    return None if matches else matches_reason


def _sha256_file(path):
    import hashlib

    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _receipt_refusal(port, platform_name=None):
    """Prove the running process was launched with the config now on disk.

    Only macOS launches through a repository-generated wrapper, so an absent
    receipt is UNKNOWN there and NOT APPLICABLE elsewhere — treating absence as
    failure on Linux would break legitimate deploys. See #361.
    """
    platform_name = platform_name or sys.platform
    expected = _sha256_file(CONFIG_FILE)
    if expected is None:
        return None
    try:
        lines = RECEIPT_FILE.read_text(encoding="utf-8").split()
    except OSError:
        if platform_name == "darwin":
            return (
                f"{REFUSED_NO_RECEIPT}: the service was not started by the "
                "repository wrapper, so the loaded configuration is unknown — "
                "run 'make litellm-restart'"
            )
        return None
    if len(lines) < 3:
        return f"{REFUSED_NO_RECEIPT}: malformed loaded-generation receipt"
    digest, started_at, receipt_port = lines[0], lines[1], lines[2]
    if receipt_port != str(port):
        return f"{REFUSED_FOREIGN_GENERATION}: receipt port {receipt_port} != {port}"
    if digest != expected:
        return (
            f"{REFUSED_STALE_GENERATION}: the running process was launched with "
            "different config bytes than the current config.yaml — run "
            "'make litellm-restart'"
        )
    try:
        if int(started_at) < int(CONFIG_FILE.stat().st_mtime):
            return (
                f"{REFUSED_STALE_GENERATION}: the launch predates the current "
                "config.yaml — run 'make litellm-restart'"
            )
    except (OSError, ValueError):
        return f"{REFUSED_NO_RECEIPT}: unreadable loaded-generation receipt"
    return None


def _deep_probe(alias, base_url, api_key, timeout=20):
    """One on-demand inference request. Never called by deploy. See #361."""
    import json as _json

    from paseo_http import open_credentialed

    body = _json.dumps(
        {
            "model": alias,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "ping"}],
        }
    ).encode()
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with open_credentialed(request, timeout) as response:
            return response.status < 300
    except Exception:
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--deep",
        action="store_true",
        help="also send one real inference request per planned alias (never used by deploy)",
    )
    args = parser.parse_args(argv)
    base_url = f"http://127.0.0.1:{os.environ.get('LITELLM_PORT', '4000')}/v1"

    plan, reason = _load_plan()
    if plan is None:
        return _fail(reason)
    routes, aliases, planned_ids = plan

    if not get_litellm_oauth_gate(os.environ):
        # The subscription lane is not in use; a direct route is legitimate.
        return 0

    refusal = (
        _plan_refusal(routes, planned_ids)
        or _receipt_refusal(os.environ.get("LITELLM_PORT", "4000"))
        or _served_refusal(aliases, base_url)
    )
    if refusal:
        return _fail(refusal)
    if args.deep:
        # On demand only: one real inference request per planned alias. Never
        # reached by deploy, so no deploy-time latency or spend. See #361.
        api_key = KEY_FILE.read_text(encoding="utf-8").strip()
        for alias in sorted(aliases):
            if not _deep_probe(alias, base_url, api_key):
                return _fail(f"refused-deep-probe-failed:{alias}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
