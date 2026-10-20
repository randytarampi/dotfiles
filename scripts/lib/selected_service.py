"""Pure, ephemeral consumer-boundary selection and route contract.

Qualification is derived afresh for each call and associated by its caller with
engine/version, operation, canonical model and route. ``discovered`` means
listed/identified only; ``protocol_probed`` means the exact protocol and
response shape were verified; ``consumer_verified`` means the real consumer
outcome was verified; ``unavailable`` is a known unusable route/service;
``unknown`` means evidence is absent, stale or failed. Alias membership alone
never qualifies support. Config-only builds without live evidence report only
discovered/unverified, never consumer_verified. Successes are not permanent.

Callers supply previously-selected state from an already-supported source
(override, verified client setting, or other repo-owned configuration); on a
fresh process they must load that source, not expect this module to persist it.
The source winning disagreement is the explicit override, then a still-present
and qualified previous selection, then ranked candidates.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal


class Operation(str, Enum):
    STT = "stt"
    TTS = "tts"
    EMBEDDING = "embedding"
    CLEANUP_LLM = "cleanup_llm"


class Qualification(str, Enum):
    DISCOVERED = "discovered"
    PROTOCOL_PROBED = "protocol_probed"
    CONSUMER_VERIFIED = "consumer_verified"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class Transport(str, Enum):
    GATEWAY = "gateway"
    DIRECT = "direct"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class CanonicalSelection:
    operation: Operation
    provider: str | None
    model: str | None
    qualification: Qualification
    reason: str


@dataclass(frozen=True)
class RouteResolution:
    transport: Transport
    endpoint: str | None = None
    wire_model: str | None = None
    key_reference: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ServiceLocator:
    selection: CanonicalSelection
    route: RouteResolution


# Approval for exceptions is code review of this explicit table.
DIRECT_EXCEPTIONS: dict[Operation, str] = {}
_RANK = {
    Qualification.DISCOVERED: 1,
    Qualification.PROTOCOL_PROBED: 2,
    Qualification.CONSUMER_VERIFIED: 3,
}


@dataclass(frozen=True)
class DirectRoute:
    """Bound direct-route facts for ``resolve_transport`` (S107 param cluster).

    Bundles the former five ``direct_*`` keyword parameters into one frozen
    value object; resolution semantics are unchanged (``endpoint=None`` keeps
    the legacy absent-facts behavior).
    """

    endpoint: str | None = None
    wire_model: str | None = None
    key_reference: str | None = None
    credential_required: bool = False
    credential_present: bool = True


def resolve_transport(
    gate: Literal["direct", "gateway"],
    qualification: Qualification,
    *,
    endpoint: str | None = None,
    wire_model: str | None = None,
    key_reference: str | None = None,
    credential_present: bool = True,
    credential_reference: str | None = None,
    credential_required: bool = True,
    direct: DirectRoute | None = None,
    operation: Operation | None = None,
) -> RouteResolution:
    """Resolve the gated route; never switch routes based on availability.

    Both routes require protocol_probed evidence or stronger, a route endpoint,
    and credentials when that route requires them. Direct exceptions require
    code review here and explicit direct-route facts. A disappeared once-
    qualified gateway is unavailable; consumers re-resolve.
    """
    if gate not in ("direct", "gateway"):
        raise ValueError("gate must be direct or gateway")
    direct_facts = direct if direct is not None else DirectRoute()
    if gate == "direct":
        return _direct_transport(
            qualification,
            direct_facts,
            endpoint,
            wire_model,
            key_reference,
            credential_present,
            credential_required,
            credential_reference,
        )
    return _gateway_transport(
        qualification,
        direct_facts,
        endpoint,
        wire_model,
        key_reference,
        credential_present,
        credential_required,
        credential_reference,
        operation,
    )


def _direct_transport(
    qualification,
    direct_facts,
    endpoint,
    wire_model,
    key_reference,
    credential_present,
    credential_required,
    credential_reference,
):
    """Bind the direct-route facts; explicit direct facts take precedence."""
    use_direct_facts = direct_facts.endpoint is not None
    return _direct_route(
        qualification,
        direct_facts.endpoint or endpoint,
        direct_facts.wire_model or wire_model,
        direct_facts.key_reference or key_reference,
        direct_facts.credential_required if use_direct_facts else credential_required,
        direct_facts.credential_present if use_direct_facts else credential_present,
        credential_reference,
    )


def _gateway_transport(
    qualification,
    direct_facts,
    endpoint,
    wire_model,
    key_reference,
    credential_present,
    credential_required,
    credential_reference,
    operation,
):
    """Resolve the gateway route; approve the registered direct exceptions."""
    qualified = _RANK.get(qualification, 0) >= _RANK[Qualification.PROTOCOL_PROBED]
    effective_reference = key_reference or credential_reference
    if (
        qualified
        and endpoint
        and (
            not credential_required
            or (credential_present and bool(effective_reference))
        )
    ):
        return RouteResolution(
            Transport.GATEWAY, endpoint, wire_model, effective_reference
        )
    if operation in DIRECT_EXCEPTIONS:
        return _direct_route(
            qualification,
            direct_facts.endpoint,
            direct_facts.wire_model,
            direct_facts.key_reference,
            direct_facts.credential_required,
            direct_facts.credential_present,
            None,
        )
    reason = "gateway unavailable: qualification, endpoint, or credential missing"
    return RouteResolution(Transport.UNAVAILABLE, reason=reason)


def _direct_route(
    qualification,
    endpoint,
    wire_model,
    key_reference,
    credential_required,
    credential_present,
    credential_reference=None,
):
    qualified = _RANK.get(qualification, 0) >= _RANK[Qualification.PROTOCOL_PROBED]
    credential_ok = not credential_required or (
        credential_present and bool(key_reference or credential_reference)
    )
    if qualified and endpoint and credential_ok:
        return RouteResolution(
            Transport.DIRECT,
            endpoint,
            wire_model,
            key_reference or credential_reference,
        )
    return RouteResolution(
        Transport.UNAVAILABLE,
        reason="direct unavailable: qualification, endpoint, or credential missing",
    )


def select(
    operation: Operation,
    candidates: list[tuple[str, str, Qualification]],
    preference: list[tuple[str, str]] | None = None,
    override: tuple[str, str] | None = None,
    previously_selected: tuple[str, str, Qualification] | None = None,
) -> CanonicalSelection:
    """Select deterministically: override, qualified previous, then ranking.

    Callers source ``previously_selected`` from supported configuration on
    fresh processes; no store is read or written here. Preference breaks ties
    after qualification rank; otherwise candidate order is retained.
    """
    by_identity = {(p, m): q for p, m, q in candidates}
    if override:
        provider, model = override
        q = by_identity.get(override, Qualification.UNKNOWN)
        return CanonicalSelection(operation, provider, model, q, "explicit override")
    if previously_selected:
        provider, model, q = previously_selected
        current_q = by_identity.get((provider, model))
        if current_q in _RANK and q in _RANK:
            return CanonicalSelection(
                operation,
                provider,
                model,
                current_q,
                "previous selection remains qualified",
            )
    preference = preference or []
    positions = {identity: index for index, identity in enumerate(preference)}
    ranked = [(p, m, q, i) for i, (p, m, q) in enumerate(candidates) if q in _RANK]
    if not ranked:
        return CanonicalSelection(
            operation, None, None, Qualification.UNAVAILABLE, "no qualified candidate"
        )
    provider, model, q, _ = max(
        ranked,
        key=lambda item: (
            _RANK[item[2]],
            -positions.get((item[0], item[1]), len(positions)),
            -item[3],
        ),
    )
    return CanonicalSelection(
        operation, provider, model, q, "highest qualification; caller order breaks ties"
    )


def resolve_stt_transport(
    flag: str | None,
    *,
    gateway_gate: bool | None,
    saved: str | None = None,
) -> tuple[str, str]:
    """Resolve the client-native STT transport axis; returns (transport, reason).

    The axis is the USER's locality/provider preference, distinct from the
    gateway-vs-direct routing policy. Precedence: an explicit ``local`` or
    ``openai`` flag binds; an omitted flag or an explicit ``auto`` defers to
    the global gateway gate (``True`` -> openai, ``False`` -> local); an
    unset gate falls to the saved selection; with nothing binding, the local
    default applies. No store is read or written here; callers persist the
    resolved choice in their canonical repo-owned configuration.
    """
    if flag in ("local", "openai"):
        return flag, f"explicit --stt-transport {flag}"
    if flag not in (None, "auto"):
        raise ValueError("stt transport flag must be local, openai, auto, or omitted")
    if saved not in (None, "local", "openai"):
        raise ValueError("saved selection must be local or openai")
    if gateway_gate is True:
        return "openai", "global gateway gate"
    if gateway_gate is False:
        return "local", "global gateway gate"
    if saved in ("local", "openai"):
        return saved, "saved selection"
    return "local", "no binding input; local default"


def assert_renders_from(selection_or_locator, rendered):
    """Raise AssertionError unless rendered route fields match the locator."""
    locator = selection_or_locator
    selection = locator.selection if isinstance(locator, ServiceLocator) else locator
    route = locator.route if isinstance(locator, ServiceLocator) else None
    expected = {
        "provider": selection.provider,
        "model": selection.model,
    }
    if route:
        expected.update(
            transport=route.transport,
            endpoint=route.endpoint,
            wire_model=route.wire_model,
        )
    for field, value in expected.items():
        actual = (
            rendered.get(field)
            if isinstance(rendered, dict)
            else getattr(rendered, field, None)
        )
        if actual != value:
            # Explicit raise (not an assert statement): the failure must
            # survive optimised bytecode (qlty bandit B101).
            raise AssertionError(f"rendered {field} {actual!r} != resolved {value!r}")


def render_invariant_test(selection_or_locator, rendered):
    """Test-mode alias for the shared consumer render invariant checker."""
    assert_renders_from(selection_or_locator, rendered)
