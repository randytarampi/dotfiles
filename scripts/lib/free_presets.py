"""Pure resolver for dynamically selected, explicitly free preset routes."""

from dataclasses import dataclass
from datetime import datetime

from litellm_config import classify_free_route

ZEN_PRESET = "omo-slim-opencode-zen-free"
ROLE_CAPABILITIES = {
    "orchestrator": frozenset({"text_output", "tool_call", "reasoning"}),
    "oracle": frozenset({"text_output", "tool_call", "reasoning"}),
    "librarian": frozenset({"text_output", "tool_call"}),
    "explorer": frozenset({"text_output", "tool_call"}),
    "coder": frozenset({"text_output", "tool_call"}),
    "task_worker": frozenset({"text_output", "tool_call"}),
    "fixer": frozenset({"text_output", "tool_call"}),
    "designer": frozenset({"text_output", "image_input"}),
    "observer": frozenset({"text_output", "image_input"}),
    "council-alpha": frozenset({"text_output", "tool_call", "reasoning"}),
    "council-beta": frozenset({"text_output", "tool_call", "reasoning"}),
    "council-gamma": frozenset({"text_output", "tool_call", "reasoning"}),
}
REQUIRED_ROLES = tuple(ROLE_CAPABILITIES)
COUNCIL_ROLES = ("council-alpha", "council-beta", "council-gamma")


@dataclass(frozen=True)
class Candidate:
    serving_ref: str
    upstream_host: str
    upstream_model: str
    provenance: str
    observed_at: datetime
    expires_at: datetime
    capabilities: dict
    source_binding_trusted: bool


@dataclass(frozen=True)
class FreePresetResolution:
    role_choices: dict
    council: tuple
    fallback: dict
    audit: dict


class FreePresetResolutionError(ValueError):
    """Raised when a required preset role has no safe candidate."""


def _bad_candidate(candidate, now):
    identity = (
        candidate.serving_ref,
        candidate.upstream_host,
        candidate.upstream_model,
        candidate.provenance,
    )
    if not all(isinstance(value, str) and value.strip() for value in identity):
        return "missing or invalid route identity/provenance"
    if (
        not isinstance(candidate.source_binding_trusted, bool)
        or not candidate.source_binding_trusted
    ):
        return "untrusted source binding"
    if not isinstance(candidate.capabilities, dict) or any(
        not isinstance(key, str) or not isinstance(value, bool)
        for key, value in candidate.capabilities.items()
    ):
        return "malformed capability metadata"
    if not isinstance(candidate.observed_at, datetime) or not isinstance(
        candidate.expires_at, datetime
    ):
        return "malformed freshness timestamps"
    try:
        if (
            candidate.observed_at.tzinfo is None
            or candidate.expires_at.tzinfo is None
            or now.tzinfo is None
        ):
            return "freshness timestamps must be timezone-aware"
        if (
            candidate.observed_at > now
            or candidate.expires_at <= now
            or candidate.expires_at <= candidate.observed_at
        ):
            return "stale or invalid freshness interval"
    except (TypeError, ValueError):
        return "incompatible freshness timestamps"
    return None


def _canonical(item):
    return item.upstream_host.strip().lower(), item.upstream_model.strip()


def _eligible_for_role(item, role):
    return all(
        item.capabilities.get(capability) is True
        for capability in ROLE_CAPABILITIES[role]
    )


def resolve_free_preset(candidates, preset, preferences, *, now):
    """Resolve one approved free preset without I/O; fail closed on ambiguity."""
    if preset not in {"free", ZEN_PRESET}:
        raise FreePresetResolutionError(f"unsupported free preset {preset!r}")
    if not isinstance(preferences, dict) or not isinstance(now, datetime):
        raise FreePresetResolutionError(
            "preferences and timezone-aware now are required"
        )
    if now.tzinfo is None or now.utcoffset() is None:
        raise FreePresetResolutionError("now must be timezone-aware")
    grouped = {}
    invalid_refs = {}
    for item in candidates:
        if not isinstance(item, Candidate):
            raise FreePresetResolutionError("candidate input is not a Candidate")
        if not isinstance(item.serving_ref, str) or not item.serving_ref.strip():
            invalid_refs[f"<invalid-{len(invalid_refs)}>"] = (
                "candidate has missing serving_ref"
            )
            continue
        grouped.setdefault(item.serving_ref, []).append(item)

    excluded = dict(invalid_refs)
    pool = []
    invalid_candidates = []
    for ref, backends in grouped.items():
        reasons = []
        identities = {
            _canonical(item)
            for item in backends
            if isinstance(item.upstream_host, str)
            and isinstance(item.upstream_model, str)
        }
        if len(identities) != 1:
            reasons.append("ambiguous serving_ref maps to multiple upstream routes")
        for item in backends:
            invalid = _bad_candidate(item, now)
            if invalid:
                reasons.append(invalid)
                invalid_candidates.append(f"{ref}: {invalid}")
                continue
            host, model = _canonical(item)
            if preset == ZEN_PRESET and host != "opencode":
                reasons.append("zen preset requires opencode upstream host")
            classification = classify_free_route(model, host, item.provenance, "preset")
            if classification["classification"] != "free-classified":
                reasons.append("backend is not free-classified")
        if reasons:
            excluded[ref] = "; ".join(sorted(set(reasons)))
            continue
        # Intersect known-positive capabilities across every record for this route.
        shared = {
            key: all(item.capabilities.get(key) is True for item in backends)
            for key in set().union(*(item.capabilities for item in backends))
        }
        pool.append(
            Candidate(
                serving_ref=min(item.serving_ref for item in backends),
                upstream_host=_canonical(backends[0])[0],
                upstream_model=_canonical(backends[0])[1],
                provenance="; ".join(sorted({item.provenance for item in backends})),
                observed_at=max(item.observed_at for item in backends),
                expires_at=min(item.expires_at for item in backends),
                capabilities=shared,
                source_binding_trusted=True,
            )
        )

    pool.sort(key=lambda item: (*_canonical(item), item.serving_ref))
    excluded = dict(sorted(excluded.items()))
    if invalid_candidates:
        raise FreePresetResolutionError(
            "invalid candidate metadata: " + "; ".join(invalid_candidates)
        )
    selected = {}
    ranked = {}
    for role in REQUIRED_ROLES:
        role_pool = [item for item in pool if _eligible_for_role(item, role)]
        raw_preference = preferences.get(role, ())
        if not isinstance(raw_preference, (list, tuple)):
            raise FreePresetResolutionError(f"invalid preferences for role {role}")
        normalized = []
        for entry in raw_preference:
            if isinstance(entry, (list, tuple)) and len(entry) == 2:
                normalized.append(
                    (str(entry[0]).strip().lower(), str(entry[1]).strip())
                )
        ranks = {key: rank for rank, key in enumerate(normalized)}
        role_pool.sort(
            key=lambda item: (
                ranks.get(_canonical(item), len(ranks)),
                *_canonical(item),
                item.serving_ref,
            )
        )
        # Equivalent transport aliases must not become extra fallback choices.
        distinct = {}
        for item in role_pool:
            distinct.setdefault(_canonical(item), item)
        role_pool = list(distinct.values())
        ranked[role] = role_pool
        if not role_pool:
            raise FreePresetResolutionError(
                f"no eligible free candidate for required role {role}"
            )
        selected[role] = role_pool[0]

    council = []
    used = set()
    for role in COUNCIL_ROLES:
        choice = next(
            (item for item in ranked[role] if _canonical(item) not in used),
            ranked[role][0],
        )
        selected[role] = choice
        council.append(choice)
        used.add(_canonical(choice))

    fallback = {
        role: tuple(
            item
            for item in ranked[role]
            if _canonical(item) != _canonical(selected[role])
        )
        for role in REQUIRED_ROLES
    }
    selected["coder"] = selected["fixer"]
    selected["task_worker"] = selected["fixer"]
    ranked["coder"] = ranked["fixer"]
    ranked["task_worker"] = ranked["fixer"]
    fallback["coder"] = fallback["fixer"]
    fallback["task_worker"] = fallback["fixer"]
    audit = {
        "excluded": excluded,
        "eligible_count": len(pool),
        "selected": {role: item.serving_ref for role, item in selected.items()},
        "preference_rank": {
            role: next(
                (
                    rank
                    for rank, item in enumerate(ranked[role])
                    if _canonical(item) == _canonical(selected[role])
                ),
                None,
            )
            for role in REQUIRED_ROLES
        },
    }
    return FreePresetResolution(selected, tuple(council), fallback, audit)
