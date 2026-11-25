#!/usr/bin/env python3
"""Read-only subscription-route preflight (#361).

Check the current OAuth report, the exact projection and managed launch
generation, and the advertised alias namespace. A launch receipt attests
the immutable-by-convention config snapshot supplied to a live listener,
not the gateway's parsed router state or dynamic backend selection.
No regeneration, restart, admin request or default inference is performed.
"""

import argparse
import hashlib
import json
import os
import subprocess  # nosec B404 - fixed managed generation checker argv.
import sys
import urllib.request
from pathlib import Path

LIB_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from constants import get_litellm_oauth_gate  # noqa: E402 -- scripts/lib bootstrap.
from free_route_manifest import config_binding_digest  # noqa: E402
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

REFUSED_NO_RECEIPT = "refused-no-loaded-generation-receipt"
REFUSED_STALE_GENERATION = "refused-stale-loaded-generation"
REFUSED_FOREIGN_GENERATION = "refused-loaded-generation-port-mismatch"


def _fail(reason):
    print(reason)
    return 1


def _load_plan():
    """Return ``((routes, aliases, planned_ids), None)`` or ``(None, reason)``."""
    try:
        raw = PROJECTION_FILE.read_bytes()
        projection = json.loads(raw)
    except FileNotFoundError:
        return None, (
            "no emitted route projection; LiteLLM has not generated a config yet "
            "— run 'make deploy'"
        )
    except (OSError, ValueError) as error:
        return None, f"route projection is unreadable ({error})"
    if not isinstance(projection, dict):
        return None, "invalid route projection"
    routes = projection.get("routes")
    if not isinstance(routes, list):
        return None, "route projection has no route list"
    try:
        binding = config_binding_digest(routes)
    except (AttributeError, TypeError, ValueError):
        return None, "invalid route projection bindings"
    if binding != projection.get("config_binding_sha256"):
        return None, "route projection binding digest differs"
    aliases = [
        row.get("model_name")
        for row in routes
        if isinstance(row, dict) and alias_model_id(row.get("model_name"))
    ]
    planned_ids = {alias_model_id(alias) for alias in aliases}
    return (routes, aliases, planned_ids, hashlib.sha256(raw).hexdigest()), None


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


def _run_generation_check(port, projection_digest):
    python = CONFIG_FILE.parent / "venv/bin/python"
    helper = Path(LIB_DIR) / "litellm_generation.py"
    try:
        result = subprocess.run(  # nosec B603 - fixed managed checker, no shell.
            [
                str(python),
                "-I",
                str(helper),
                "check",
                str(CONFIG_FILE.parent),
                str(port),
                projection_digest,
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return REFUSED_NO_RECEIPT
    if result.returncode == 0:
        return None
    reason = result.stdout.strip()
    if reason in {
        REFUSED_NO_RECEIPT,
        REFUSED_STALE_GENERATION,
        REFUSED_FOREIGN_GENERATION,
        "refused-loaded-generation-projection-mismatch",
        "refused-loaded-generation-process-mismatch",
    }:
        return reason
    return REFUSED_NO_RECEIPT


def _receipt_refusal(port, projection_digest, platform_name=None):
    """Unsupported, unreadable or unverified launch evidence is UNKNOWN."""
    platform_name = platform_name or sys.platform
    if platform_name not in {"darwin", "linux"}:
        return f"{REFUSED_NO_RECEIPT}: unmanaged platform; generation UNKNOWN"
    reason = _run_generation_check(port, projection_digest)
    if reason:
        return (
            f"{reason}: managed launch generation is unknown or stale — "
            "run 'make deploy' to install the launcher, then 'make litellm-restart'"
        )
    return None


def _deep_probe(alias, base_url, api_key, timeout=20):
    """One on-demand inference request. Never called by deploy. See #361."""
    from paseo_http import open_credentialed

    body = json.dumps(
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
            return 200 <= response.status < 300
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
    routes, aliases, planned_ids, projection_digest = plan

    if not get_litellm_oauth_gate(os.environ):
        # The subscription lane is not in use; a direct route is legitimate.
        return 0

    port = os.environ.get("LITELLM_PORT", "4000")
    refusal = (
        _plan_refusal(routes, planned_ids)
        or _receipt_refusal(port, projection_digest)
        or _served_refusal(aliases, base_url)
        or _receipt_refusal(port, projection_digest)
    )
    if refusal:
        return _fail(refusal)
    if args.deep:
        # On demand only: one real inference request per planned alias. Never
        # reached by deploy, so no deploy-time latency or spend. See #361.
        try:
            api_key = KEY_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            return _fail("refused-deep-probe-key-unavailable")
        if not api_key:
            return _fail("refused-deep-probe-key-unavailable")
        for alias in sorted(set(aliases)):
            if not _deep_probe(alias, base_url, api_key):
                return _fail(f"refused-deep-probe-failed:{alias}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
