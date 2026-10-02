"""Pure, provider-aware model-reference comparison primitives.

References retain their endpoint namespace and credential-scope label so equal
model strings from different APIs or keys never imply shared evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping


class Outcome(str, Enum):
    MATCH = "MATCH"
    MISSING = "MISSING"
    UNKNOWN = "UNKNOWN"
    SKIPPED_INACTIVE = "SKIPPED_INACTIVE"


@dataclass(frozen=True, order=True)
class EndpointIdentity:
    """Safe endpoint identity; credential_scope is a label, never a secret."""

    namespace: str
    credential_scope: str


@dataclass(frozen=True, order=True)
class ModelReference:
    endpoint: EndpointIdentity
    wire_id: str
    source: str
    field: str


@dataclass(frozen=True)
class Catalogue:
    """One endpoint's fetch disposition and successful, complete ID set."""

    successful: bool
    model_ids: frozenset[str] = frozenset()
    http_status: int | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ReferenceResult:
    reference: ModelReference
    outcome: Outcome
    reason: str | None = None


@dataclass(frozen=True)
class Comparison:
    results: tuple[ReferenceResult, ...]
    complete: bool


def openai_zen_direct_id(wire_id: str) -> str:
    """Remove at most one OpenCode provider wrapper for direct OpenAI/Zen IDs."""
    for prefix in ("openai/", "opencode/"):
        if wire_id.startswith(prefix):
            return wire_id[len(prefix) :]
    return wire_id


def google_direct_id(wire_id: str) -> str:
    """Accept Google's documented one-time ``models/`` resource prefix."""
    return wire_id[len("models/") :] if wire_id.startswith("models/") else wire_id


def compare_references(
    references: Iterable[ModelReference],
    catalogues: Mapping[EndpointIdentity, Catalogue],
    *,
    inactive: frozenset[EndpointIdentity] = frozenset(),
    normalize_id=lambda _endpoint, wire_id: wire_id,
) -> Comparison:
    """Compare every requested complete wire ID with its endpoint's ID set.

    Missing evidence is UNKNOWN, not an empty catalogue. A known mismatch
    remains MISSING regardless of unknown results for other endpoints.
    """
    ordered = sorted(set(references))
    results = []
    for reference in ordered:
        endpoint = reference.endpoint
        if endpoint in inactive:
            results.append(ReferenceResult(reference, Outcome.SKIPPED_INACTIVE))
            continue
        catalogue = catalogues.get(endpoint)
        if catalogue is None or not catalogue.successful:
            results.append(
                ReferenceResult(
                    reference,
                    Outcome.UNKNOWN,
                    catalogue.reason if catalogue else "catalogue unavailable",
                )
            )
            continue
        requested_id = normalize_id(endpoint, reference.wire_id)
        known_ids = {
            normalize_id(endpoint, model_id) for model_id in catalogue.model_ids
        }
        outcome = Outcome.MATCH if requested_id in known_ids else Outcome.MISSING
        results.append(ReferenceResult(reference, outcome))
    return Comparison(
        tuple(results), all(item.outcome != Outcome.UNKNOWN for item in results)
    )
