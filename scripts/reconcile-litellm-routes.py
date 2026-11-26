#!/usr/bin/env python3
"""Read-only subscription-route preflight (#361).

Check the current OAuth report, the exact projection and managed launch
generation, and the advertised alias namespace. A launch receipt attests
the immutable-by-convention config snapshot supplied to a live listener,
not the gateway's parsed router state or dynamic backend selection.
No regeneration, restart, admin request or default inference is performed.

This is the fleet adapter only. The checks themselves live in
``scripts/lib/litellm_route_preflight.py`` so the direct client writers cannot
drift from what ``configure-all.sh`` enforces.
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

import litellm_route_preflight as preflight  # noqa: E402 -- lib bootstrap.
from constants import get_litellm_oauth_gate  # noqa: E402
from litellm_config import (  # noqa: E402 -- scripts/lib bootstrap.
    chatgpt_verified_openai_models_report,
    oauth_cache_expiry,
)
from litellm_route_preflight import (  # noqa: E402 -- re-exported for callers.
    REFUSED_NO_RECEIPT,
    REFUSED_STALE_GENERATION,
)

LITELLM_DIR = Path("~/.local/share/litellm").expanduser()
PROJECTION_FILE = LITELLM_DIR / "free-route-config.json"
KEY_FILE = LITELLM_DIR / "clients" / "opencode.key"
CONFIG_FILE = LITELLM_DIR / "config.yaml"


def _fail(reason):
    print(reason)
    return 1


def _read_gateway_key():
    """Return ``(key, None)`` or ``(None, reason)`` for this client's key file."""
    try:
        return KEY_FILE.read_text(encoding="utf-8").strip(), None
    except OSError as error:
        return None, f"cannot read gateway client key ({error})"


def _run_generation_check(port, projection_digest):
    return preflight.run_generation_check(CONFIG_FILE.parent, port, projection_digest)


def _receipt_refusal(port, projection_digest, platform_name=None):
    """Unsupported, unreadable or unverified launch evidence is UNKNOWN."""
    return preflight.receipt_refusal(
        port,
        projection_digest,
        platform_name,
        state_dir=CONFIG_FILE.parent,
        run_check=_run_generation_check,
    )


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
    port = os.environ.get("LITELLM_PORT", "4000")
    base_url = f"http://127.0.0.1:{port}/v1"

    plan, reason = preflight.load_plan(PROJECTION_FILE)
    if plan is None:
        return _fail(reason)

    authorization = preflight.authorize_plan(
        plan,
        environ=os.environ,
        port=port,
        base_url=base_url,
        key_reader=_read_gateway_key,
        platform_name=sys.platform,
        state_dir=CONFIG_FILE.parent,
        run_check=_run_generation_check,
        oauth_expiry=oauth_cache_expiry,
        report=chatgpt_verified_openai_models_report,
        gate=get_litellm_oauth_gate,
    )
    if not authorization.permitted:
        return _fail(authorization.reason)
    if args.deep and authorization.state == preflight.AUTHORIZED:
        # On demand only: one real inference request per planned alias. Never
        # reached by deploy, so no deploy-time latency or spend. See #361.
        try:
            api_key = KEY_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            return _fail("refused-deep-probe-key-unavailable")
        if not api_key:
            return _fail("refused-deep-probe-key-unavailable")
        for alias in sorted(set(plan.aliases)):
            if not _deep_probe(alias, base_url, api_key):
                return _fail(f"refused-deep-probe-failed:{alias}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
