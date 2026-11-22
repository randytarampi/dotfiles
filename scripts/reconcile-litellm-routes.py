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
from pathlib import Path

LIB_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from constants import get_litellm_oauth_gate  # noqa: E402 -- scripts/lib bootstrap.
from litellm_config import (  # noqa: E402 -- scripts/lib bootstrap.
    CHATGPT_REPORT_OK,
    chatgpt_verified_openai_models_report,
)
from litellm_route_guard import (  # noqa: E402 -- scripts/lib bootstrap.
    REFUSED_AMBIGUOUS_CREDENTIALS,
    REFUSED_REVOKED,
    REFUSED_UNUSABLE_REPORT,
    alias_model_id,
    assert_namespace_matches,
    planned_subscription_bindings,
)

LITELLM_DIR = Path("~/.local/share/litellm").expanduser()
PROJECTION_FILE = LITELLM_DIR / "free-route-config.json"
KEY_FILE = LITELLM_DIR / "clients" / "opencode.key"


def _fail(reason):
    print(reason)
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)

    port = os.environ.get("LITELLM_PORT", "4000")
    base_url = f"http://127.0.0.1:{port}/v1"

    try:
        projection = json.loads(PROJECTION_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _fail(
            "no emitted route projection; LiteLLM has not generated a config yet "
            "— run 'make deploy'"
        )
    except (OSError, ValueError) as error:
        return _fail(f"route projection is unreadable ({error})")

    routes = projection.get("routes")
    if not isinstance(routes, list):
        return _fail("route projection has no route list")

    aliases = [
        row.get("model_name")
        for row in routes
        if isinstance(row, dict) and alias_model_id(row.get("model_name"))
    ]
    planned_ids = {alias_model_id(alias) for alias in aliases}

    if not get_litellm_oauth_gate(os.environ):
        # The subscription lane is not in use; a direct route is legitimate.
        return 0

    if (os.environ.get("OPENAI_API_KEY") or "").strip() and planned_ids:
        return _fail(
            f"{REFUSED_AMBIGUOUS_CREDENTIALS}: subscription aliases are planned "
            "while a direct paid OPENAI_API_KEY is set — unset the key or "
            "disable the OAuth gate"
        )

    if planned_ids:
        # Bind the plan to the *current* report. A projection that outlives the
        # authorisation it was built from must not authorise a client.
        state, verified = chatgpt_verified_openai_models_report(os.environ)
        if state != CHATGPT_REPORT_OK:
            return _fail(
                f"{REFUSED_UNUSABLE_REPORT}: verified-model report is {state} "
                "while subscription aliases are planned"
            )
        revoked = sorted(planned_ids - set(verified))
        if revoked:
            return _fail(f"{REFUSED_REVOKED}:{','.join(revoked[:3])}")
        newer = sorted(set(verified) - planned_ids)
        if newer:
            print(
                "warning: report verifies ids absent from the projection "
                f"({','.join(newer[:3])}); run 'make deploy' to regenerate"
            )
        wires_ok, wires_reason = planned_subscription_bindings(routes)
        if not wires_ok:
            return _fail(wires_reason)

    try:
        api_key = KEY_FILE.read_text(encoding="utf-8").strip()
    except OSError as error:
        return _fail(f"cannot read gateway client key ({error})")
    if not api_key:
        return _fail("gateway client key is empty")

    from model_catalogues import get_models  # noqa: E402 -- scripts/lib bootstrap.

    served = get_models(f"{base_url}/models", api_key)
    matches, matches_reason = assert_namespace_matches(aliases, served)
    if not matches:
        return _fail(matches_reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
