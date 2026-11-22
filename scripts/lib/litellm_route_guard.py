"""Gateway route qualification for subscription-routed model aliases.

A generated config, a running daemon and a served alias are three different
facts. Only the last authorises client generation, so this module inspects the
running LiteLLM proxy instead of trusting on-disk YAML or a cached observation.

Inspection is deliberately local: ``GET /model/info`` answers from the router
state loaded at startup and never contacts an upstream provider, so the
already-qualified path works on an air-gapped host. See #361.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from constants import get_litellm_oauth_gate
from litellm_config import (
    CHATGPT_REPORT_OK,
    chatgpt_verified_openai_models_report,
    oauth_cache_expiry,
)

SUBSCRIPTION_BINDING = "chatgpt"
DIRECT_OPENAI_PREFIX = "openai/"

QUALIFIED = "qualified"
NO_ROUTES_REQUESTED = "no-subscription-routes-requested"
SUBSCRIPTION_REQUIRED = "subscription-required"

REFUSED_AMBIGUOUS_CREDENTIALS = "refused-ambiguous-direct-credentials"
REFUSED_UNUSABLE_REPORT = "refused-unusable-report"
REFUSED_UNQUALIFIED = "refused-unqualified-subscription-ids"
REFUSED_UNKNOWN_SERVICE = "refused-unknown-service-state"
REFUSED_MISSING_ALIAS = "refused-alias-not-served"
REFUSED_WRONG_BINDING = "refused-alias-not-subscription-bound"
REFUSED_MIXED_BINDING = "refused-alias-has-multiple-upstreams"


def requested_openai_ids(model_refs):
    """Return the model IDs requested through the ``openai/`` namespace."""
    ids = set()
    for ref in model_refs:
        if not isinstance(ref, str) or not ref.startswith(DIRECT_OPENAI_PREFIX):
            continue
        model_id = ref.split("/", 1)[1]
        if model_id:
            ids.add(model_id)
    return ids


def route_expectation(environ, model_refs):
    """Return ``(state, expected_aliases)`` for subscription-routed refs.

    ``expected_aliases`` is the intersection of what this configuration
    requests and what the report proves is verified. Verified IDs that are not
    requested are never required — publishing them is not a goal.
    """
    requested = requested_openai_ids(model_refs)
    if not requested:
        return NO_ROUTES_REQUESTED, frozenset()
    if not get_litellm_oauth_gate(environ):
        return NO_ROUTES_REQUESTED, frozenset()
    if (environ.get("OPENAI_API_KEY") or "").strip():
        # The same alias silently becomes the direct paid route. Equality of
        # the report digest cannot authorise that switch, so refuse outright.
        return REFUSED_AMBIGUOUS_CREDENTIALS, frozenset()
    if oauth_cache_expiry("chatgpt", environ) is None:
        return NO_ROUTES_REQUESTED, frozenset()
    state, verified = chatgpt_verified_openai_models_report(environ)
    if state != CHATGPT_REPORT_OK:
        return REFUSED_UNUSABLE_REPORT, frozenset()
    expected = frozenset(
        f"{DIRECT_OPENAI_PREFIX}{model_id}" for model_id in requested & set(verified)
    )
    if not expected:
        # An ok-but-empty intersection proves no requested ID is verified. It
        # is not authorisation to select a subscription model anyway.
        return REFUSED_UNQUALIFIED, frozenset()
    return SUBSCRIPTION_REQUIRED, expected


def served_routes(base_url, api_key, timeout=5.0):
    """Map alias -> set of upstream binding prefixes, or ``None`` when unknown.

    ``None`` means UNKNOWN, never "no routes"; callers must fail closed.
    """
    if not base_url or not api_key:
        return None
    url = base_url.rstrip("/") + "/model/info"
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {api_key}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError):
        return None
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return None
    routes = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        alias = row.get("model_name")
        upstream = (row.get("litellm_params") or {}).get("model")
        if not isinstance(alias, str) or not isinstance(upstream, str):
            continue
        routes.setdefault(alias, set()).add(upstream.split("/", 1)[0])
    return routes


def assert_gateway_routes(environ, model_refs, *, base_url, api_key, timeout=5.0):
    """Return ``(ok, reason)`` proving expected aliases bind to the subscription.

    Every route that could satisfy an expected alias must be subscription-bound;
    a single direct or mixed deployment is enough to refuse.
    """
    state, expected = route_expectation(environ, model_refs)
    if state == NO_ROUTES_REQUESTED:
        return True, state
    if state != SUBSCRIPTION_REQUIRED:
        return False, state
    routes = served_routes(base_url, api_key, timeout=timeout)
    if routes is None:
        return False, REFUSED_UNKNOWN_SERVICE
    for alias in sorted(expected):
        bound = routes.get(alias)
        if not bound:
            return False, f"{REFUSED_MISSING_ALIAS}:{alias}"
        if len(bound) > 1:
            return False, f"{REFUSED_MIXED_BINDING}:{alias}"
        if bound != {SUBSCRIPTION_BINDING}:
            return False, f"{REFUSED_WRONG_BINDING}:{alias}"
    return True, QUALIFIED
