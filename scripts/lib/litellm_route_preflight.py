"""One read-only gateway-authorisation contract for every client writer (#361).

``scripts/reconcile-litellm-routes.py`` preflights the fleet before
``configure-all.sh`` lets any gateway-only client write, but the direct client
generators had no check of their own: a stale, revoked or unattestable
subscription generation could still become new ``opencode.json`` or Pi
``settings.json``/``models.json`` content. Both now ask this module, and the
command-line preflight is a thin adapter over :func:`authorize_plan`, so a
client script cannot disagree with the orchestrator about what "authorised"
means.

What each side can attest is unchanged. Backend identity is a plan-side
property: an ``openai/<id>`` alias must bind exactly ``chatgpt/<id>``, and that
is asserted against the generated projection. Runtime evidence is only that
the running gateway advertises the planned alias namespace to *that client's
own key*; ``/v1/models`` never attests backend identity. Launch evidence is the
managed generation receipt for the projection actually installed.

Read-only. There is no regeneration, restart, credentialed admin request or
inference here, and no credential value is logged. Every missing, unreadable or
unattestable input is refused as UNKNOWN; none of them is treated as success.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess  # nosec B404 - fixed managed generation checker argv.
import sys
from dataclasses import dataclass
from pathlib import Path

from constants import get_litellm_oauth_gate
from free_route_manifest import config_binding_digest
from litellm_client_auth import resolve_client_auth
from litellm_config import (
    CHATGPT_REPORT_OK,
    chatgpt_verified_openai_models_report,
    oauth_cache_expiry,
)
from litellm_route_guard import (
    PLANNED_BINDINGS_VERIFIED,
    REFUSED_AMBIGUOUS_CREDENTIALS,
    REFUSED_EXPIRED_AUTH,
    REFUSED_REVOKED,
    REFUSED_UNKNOWN_SERVICE,
    REFUSED_UNUSABLE_REPORT,
    alias_model_id,
    assert_namespace_matches,
    planned_subscription_bindings,
)

LITELLM_DIR = Path("~/.local/share/litellm").expanduser()
PROJECTION_FILE = LITELLM_DIR / "free-route-config.json"
CONFIG_FILE = LITELLM_DIR / "config.yaml"

# Per-client credentials are not interchangeable: each gateway key is scoped
# to one consumer, so a Pi-only deployment must never be asked to hold the
# OpenCode key. See ``litellm_client_auth.DEFAULT_CLIENT_FILES``.
CLIENT_KEY_ENVS = {
    "opencode": "LITELLM_OPENCODE_KEY",
    "pi": "LITELLM_PI_KEY",
}


def litellm_state_dir(environ=None):
    """Resolve the installed LiteLLM state directory for this environment."""
    environ = os.environ if environ is None else environ
    home = Path(environ.get("HOME", str(Path.home()))).expanduser()
    return home / ".local/share/litellm"


def default_projection_file(environ=None):
    """Resolve the emitted route projection for this environment."""
    return litellm_state_dir(environ) / "free-route-config.json"


REFUSED_NO_RECEIPT = "refused-no-loaded-generation-receipt"
REFUSED_STALE_GENERATION = "refused-stale-loaded-generation"
REFUSED_FOREIGN_GENERATION = "refused-loaded-generation-port-mismatch"
REFUSED_PROJECTION_MISMATCH = "refused-loaded-generation-projection-mismatch"
REFUSED_PROCESS_MISMATCH = "refused-loaded-generation-process-mismatch"

MANAGED_GENERATION_REFUSALS = frozenset(
    {
        REFUSED_NO_RECEIPT,
        REFUSED_STALE_GENERATION,
        REFUSED_FOREIGN_GENERATION,
        REFUSED_PROJECTION_MISMATCH,
        REFUSED_PROCESS_MISMATCH,
    }
)

# Authorisation states. Only the first is a verified generation; the second
# says the subscription lane is not in use, so a direct route is legitimate.
# UNKNOWN is never a pass: it means the evidence could not be established.
AUTHORIZED = "authorized"
NOT_APPLICABLE = "not-applicable"
REFUSED = "refused"
UNKNOWN = "unknown"

NOT_APPLICABLE_REASON = "subscription-lane-not-in-use"


@dataclass(frozen=True)
class RoutePlan:
    """The emitted route projection, bound to its own digest."""

    routes: tuple
    aliases: tuple
    planned_ids: frozenset
    digest: str


@dataclass(frozen=True)
class GatewayAuthorization:
    """Result of one read-only authorisation attempt."""

    state: str
    reason: str = ""
    projection_digest: str = ""

    @property
    def permitted(self):
        """Whether new gateway-only client configuration may be written."""
        return self.state in {AUTHORIZED, NOT_APPLICABLE}


def load_plan(projection_file=None):
    """Return ``(RoutePlan, None)`` or ``(None, reason)`` for the projection."""
    path = PROJECTION_FILE if projection_file is None else Path(projection_file)
    try:
        raw = path.read_bytes()
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
    return (
        RoutePlan(
            routes=tuple(routes),
            aliases=tuple(aliases),
            planned_ids=frozenset(planned_ids),
            digest=hashlib.sha256(raw).hexdigest(),
        ),
        None,
    )


def plan_refusal(routes, planned_ids, environ=None, *, oauth_expiry=None, report=None):
    """Return a reason when the plan is not authorised by the current report."""
    environ = os.environ if environ is None else environ
    oauth_expiry = oauth_cache_expiry if oauth_expiry is None else oauth_expiry
    report = chatgpt_verified_openai_models_report if report is None else report
    if (environ.get("OPENAI_API_KEY") or "").strip() and planned_ids:
        return (
            f"{REFUSED_AMBIGUOUS_CREDENTIALS}: subscription aliases are planned "
            "while a direct paid OPENAI_API_KEY is set — unset the key or "
            "disable the OAuth gate"
        )
    if not planned_ids:
        return None
    if oauth_expiry("chatgpt", environ) is None:
        # Generation would not emit these routes without a live token, so a
        # plan that still names them is stale authorisation.
        return (
            f"{REFUSED_EXPIRED_AUTH}: chatgpt OAuth cache is absent or expired "
            "while subscription aliases are planned — run "
            "scripts/litellm-oauth.py --provider chatgpt"
        )
    # A projection that outlives the authorisation it was built from must not
    # authorise a client.
    state, verified = report(environ)
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
    wires_ok, wires_reason = planned_subscription_bindings(list(routes))
    return None if wires_ok else wires_reason


def served_refusal(aliases, base_url, api_key, *, catalogue=None):
    """Return a reason when the gateway does not advertise the plan.

    ``catalogue`` carries an advertisement set the caller already read with
    the same client key, so a second writer in one run does not repeat the
    request. ``None`` still means UNKNOWN and is never treated as empty.
    """
    if catalogue is None:
        if not api_key:
            return "gateway client key is empty"
        from model_catalogues import get_models

        catalogue = get_models(f"{base_url}/models", api_key)
    matches, matches_reason = assert_namespace_matches(aliases, catalogue)
    return None if matches else matches_reason


def run_generation_check(
    state_dir, port, projection_digest, *, python=None, helper=None, timeout=15
):
    """Run the managed generation checker; ``None`` means the receipt is fresh."""
    state_dir = Path(state_dir).expanduser()
    interpreter = state_dir / "venv/bin/python" if python is None else Path(python)
    helper_path = (
        Path(__file__).resolve().with_name("litellm_generation.py")
        if helper is None
        else Path(helper)
    )
    try:
        result = subprocess.run(  # nosec B603 - fixed managed checker, no shell.
            [
                str(interpreter),
                "-I",
                str(helper_path),
                "check",
                str(state_dir),
                str(port),
                projection_digest,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return REFUSED_NO_RECEIPT
    if result.returncode == 0:
        return None
    reason = result.stdout.strip()
    if reason in MANAGED_GENERATION_REFUSALS:
        return reason
    return REFUSED_NO_RECEIPT


def receipt_refusal(
    port, projection_digest, platform_name=None, *, state_dir=None, run_check=None
):
    """Unsupported, unreadable or unverified launch evidence is UNKNOWN."""
    platform_name = platform_name or sys.platform
    if platform_name not in {"darwin", "linux"}:
        return f"{REFUSED_NO_RECEIPT}: unmanaged platform; generation UNKNOWN"
    if run_check is None:
        state_dir = LITELLM_DIR if state_dir is None else state_dir
        reason = run_generation_check(state_dir, port, projection_digest)
    else:
        reason = run_check(port, projection_digest)
    if reason:
        return (
            f"{reason}: managed launch generation is unknown or stale — "
            "run 'make deploy' to install the launcher, then 'make litellm-restart'"
        )
    return None


def _client_key_reader(client):
    """Return a reader for one client's own gateway credential."""

    def read():
        env_name = CLIENT_KEY_ENVS.get(client)
        if env_name is None:
            return None, f"unknown gateway client {client!r}"
        auth = resolve_client_auth(env_name)
        if auth.state == "conflict":
            return None, f"gateway client key is ambiguous ({auth.detail})"
        if not auth.authorized:
            return None, f"gateway client key unavailable ({auth.detail})"
        return auth.value, None

    return read


def authorize_plan(
    plan,
    *,
    environ=None,
    port=None,
    base_url=None,
    client=None,
    api_key=None,
    key_reader=None,
    catalogue=None,
    platform_name=None,
    state_dir=None,
    run_check=None,
    oauth_expiry=None,
    report=None,
    gate=None,
):
    """Compose every check against one projection. Never mutates anything."""
    environ = os.environ if environ is None else environ
    port = port or environ.get("LITELLM_PORT", "4000")
    base_url = base_url or f"http://127.0.0.1:{port}/v1"
    gate = get_litellm_oauth_gate if gate is None else gate
    if not gate(environ):
        # The subscription lane is not in use; a direct route is legitimate.
        return GatewayAuthorization(NOT_APPLICABLE, NOT_APPLICABLE_REASON, plan.digest)
    refusal = plan_refusal(
        plan.routes,
        plan.planned_ids,
        environ,
        oauth_expiry=oauth_expiry,
        report=report,
    )
    if refusal:
        return GatewayAuthorization(REFUSED, refusal, plan.digest)
    refusal = receipt_refusal(
        port,
        plan.digest,
        platform_name,
        state_dir=state_dir,
        run_check=run_check,
    )
    if refusal:
        return GatewayAuthorization(REFUSED, refusal, plan.digest)
    if api_key is None:
        reader = key_reader
        if reader is None:
            if client is None:
                return GatewayAuthorization(
                    UNKNOWN,
                    "no gateway client identity to resolve a credential for",
                    plan.digest,
                )
            reader = _client_key_reader(client)
        api_key, key_reason = reader()
        if key_reason:
            return GatewayAuthorization(UNKNOWN, key_reason, plan.digest)
    refusal = served_refusal(plan.aliases, base_url, api_key, catalogue=catalogue)
    if refusal:
        state = UNKNOWN if refusal.startswith(REFUSED_UNKNOWN_SERVICE) else REFUSED
        return GatewayAuthorization(state, refusal, plan.digest)
    # Re-check the receipt at the consumer boundary: a generation that changed
    # while the advertisement was read attested a different plan than the one
    # that answered.
    refusal = receipt_refusal(
        port,
        plan.digest,
        platform_name,
        state_dir=state_dir,
        run_check=run_check,
    )
    if refusal:
        return GatewayAuthorization(REFUSED, refusal, plan.digest)
    return GatewayAuthorization(AUTHORIZED, PLANNED_BINDINGS_VERIFIED, plan.digest)


def authorize_gateway_client(
    client, *, environ=None, projection_file=None, state_dir=None, **kwargs
):
    """Authorise new gateway-only configuration for one direct client write.

    The LiteLLM state directory and the managed-launcher path are resolved from
    the supplied environment's ``HOME`` at call time, so a generator that runs
    under an isolated home reads that home's evidence rather than the importing
    process's. The command-line preflight keeps its own module-level paths.
    """
    environ = os.environ if environ is None else environ
    plan, reason = load_plan(
        default_projection_file(environ) if projection_file is None else projection_file
    )
    if plan is None:
        return GatewayAuthorization(UNKNOWN, reason)
    if state_dir is None:
        state_dir = litellm_state_dir(environ)
    return authorize_plan(
        plan, client=client, environ=environ, state_dir=state_dir, **kwargs
    )
