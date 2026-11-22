"""Subscription-route authorisation, split by what each side can attest.

Backend identity is a **plan-side** property. For an ``openai/<id>`` alias the
only admissible wire model on the subscription lane is exactly
``chatgpt/<id>``, and that is asserted against the generator's own entries
before anything is written. There is no need for a runtime probe to establish
it, because the value is determined by the alias.

Runtime proof is only that the running gateway **advertises** the alias to the
client key. ``/v1/models`` advertises aliases and does not attest backend
identity, so it is never used here to claim more than presence.

Checks do not mutate. There is no regeneration, no restart, and no
credentialed admin endpoint in this module: the repository already asserts that
``/model/info`` is an unsafe destination for an authenticated metadata GET
(``scripts/lib/tests/test_strictfree_evidence.py``), and reusing it would
reintroduce a credential path the codebase deliberately excluded. See #361.
"""

from constants import get_litellm_oauth_gate

ALIAS_PREFIX = "openai/"
SUBSCRIPTION_WIRE_PREFIX = "chatgpt/"

NO_SUBSCRIPTION_REQUESTED = "no-subscription-alias-requested"
NOT_APPLICABLE = "subscription-lane-not-in-use"
PLANNED_BINDINGS_VERIFIED = "planned-subscription-bindings-verified"
QUALIFIED = "qualified"

REFUSED_AMBIGUOUS_CREDENTIALS = "refused-ambiguous-direct-credentials"
REFUSED_MALFORMED_ENTRY = "refused-malformed-alias-entry"
REFUSED_DUPLICATE_ALIAS = "refused-duplicate-alias-bindings"
REFUSED_WIRE_MISMATCH = "refused-wire-model-mismatch"
REFUSED_ALIAS_NOT_ADVERTISED = "refused-alias-not-advertised"
REFUSED_UNKNOWN_SERVICE = "refused-unknown-service-state"
REFUSED_STALE_ALIAS = "refused-stale-advertised-alias"


def alias_model_id(alias):
    """Return the bare model ID for an ``openai/<id>`` alias, else ``None``."""
    if not isinstance(alias, str) or not alias.startswith(ALIAS_PREFIX):
        return None
    return alias[len(ALIAS_PREFIX) :] or None


def planned_subscription_bindings(entries):
    """Assert every ``openai/<id>`` entry binds exactly ``chatgpt/<id>``.

    Duplicates, mistyped wires and malformed rows are refused: a single good
    row must not launder a bad one sitting beside it. Entries carrying an
    explicit ``service_tier`` are skipped — service lanes are a separate,
    deliberately direct transport that the subscription lane does not own.
    """
    bindings = {}
    malformed = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            malformed.append("non-object")
            continue
        params = entry.get("litellm_params")
        if not isinstance(params, dict):
            malformed.append(str(entry.get("model_name")))
            continue
        if params.get("service_tier"):
            continue
        alias = entry.get("model_name")
        wire = params.get("model")
        if not isinstance(alias, str) or not isinstance(wire, str) or not wire:
            malformed.append(str(alias))
            continue
        bindings.setdefault(alias, []).append(wire)
    if malformed:
        return False, f"{REFUSED_MALFORMED_ENTRY}:{','.join(malformed[:3])}"
    checked = 0
    for alias, wires in bindings.items():
        model_id = alias_model_id(alias)
        if model_id is None:
            continue
        checked += 1
        if len(wires) != 1:
            return False, f"{REFUSED_DUPLICATE_ALIAS}:{alias}"
        expected = f"{SUBSCRIPTION_WIRE_PREFIX}{model_id}"
        if wires[0] != expected:
            return False, f"{REFUSED_WIRE_MISMATCH}:{alias}"
    if not checked:
        return True, NO_SUBSCRIPTION_REQUESTED
    return True, PLANNED_BINDINGS_VERIFIED


def assert_subscription_wires(environ, entries):
    """Guard the authoritative producer: no paid or mistyped wire under OAuth.

    Only applies while the subscription lane is the intended transport. A
    deliberately configured direct route, with the OAuth gate off or a paid key
    present, is not this function's business.
    """
    intended = (
        get_litellm_oauth_gate(environ)
        and not (environ.get("OPENAI_API_KEY") or "").strip()
    )
    if not intended:
        return True, NOT_APPLICABLE
    return planned_subscription_bindings(entries)


def openai_namespace(aliases):
    """The subset of aliases owned by the ``openai/`` namespace."""
    return {alias for alias in aliases or [] if alias_model_id(alias)}


def assert_namespace_matches(planned, served):
    """The gateway must advertise exactly the planned ``openai`` aliases.

    Stricter than presence: an alias still advertised after it left the plan is
    a stale service — the report no longer authorises it, so it must not remain
    selectable. This is the runtime half of the report-shrink defence.
    """
    if served is None:
        return False, REFUSED_UNKNOWN_SERVICE
    planned_ids = openai_namespace(planned)
    served_ids = openai_namespace(served)
    missing = sorted(planned_ids - served_ids)
    if missing:
        return False, f"{REFUSED_ALIAS_NOT_ADVERTISED}:{','.join(missing[:3])}"
    stale = sorted(served_ids - planned_ids)
    if stale:
        return False, f"{REFUSED_STALE_ALIAS}:{','.join(stale[:3])}"
    return True, QUALIFIED
