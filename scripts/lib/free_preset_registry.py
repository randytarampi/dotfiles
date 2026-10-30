"""Pure materialization of dynamically resolved free preset routes."""

import copy

from free_presets import (
    COUNCIL_ROLES,
    REQUIRED_ROLES,
    FreePresetResolutionError,
    resolve_free_preset,
)

PRESETS = ("free", "omo-slim-opencode-zen-free")
ROLE_NAMES = {
    "council-alpha": "alpha",
    "council-beta": "beta",
    "council-gamma": "gamma",
}


def _ref(candidate):
    """Preserve the resolver's exact selected serving reference."""
    return candidate.serving_ref


def _configured_ref(value, candidates):
    """Match exact configured serving refs or canonical upstream identities."""
    if not isinstance(value, str) or "/" not in value:
        return None
    provider, model = value.split("/", 1)
    for candidate in candidates:
        identity = (
            candidate.upstream_host.strip().lower(),
            candidate.upstream_model.strip(),
        )
        if candidate.serving_ref == value:
            return identity
        if identity[0] == provider.lower() and identity[1] in {value, model}:
            return identity
    return None


def _preferences(registry, preset, candidates):
    roles = registry["presets"][preset]
    tier = registry["_tiers"][preset]
    prefs = {}
    for role in REQUIRED_ROLES:
        config_role = ROLE_NAMES.get(role, role)
        ordered = []
        assigned = roles.get(config_role, {}).get("model")
        council = tier["council"]["presets"][preset].get(config_role, {}).get("model")
        for ref in (assigned, council):
            key = _configured_ref(ref, candidates)
            if key and key not in ordered:
                ordered.append(key)
        for ref in tier["fallback"].get(config_role, []):
            key = _configured_ref(ref, candidates)
            if key and key not in ordered:
                ordered.append(key)
        prefs[role] = ordered
    return prefs


def _require_schema(registry, preset):
    try:
        role_config = registry["presets"][preset]
        tier = registry["_tiers"][preset]
        council = tier["council"]
        choices = council["presets"][preset]
        fallback = tier["fallback"]
    except (KeyError, TypeError) as error:
        if preset in PRESETS:
            return
        raise FreePresetResolutionError(
            f"preset {preset!r} is missing required registry schema"
        ) from error
    if not all(
        isinstance(value, dict)
        for value in (role_config, tier, council, choices, fallback)
    ):
        raise FreePresetResolutionError(
            f"preset {preset!r} has invalid registry schema"
        )
    for role in (
        "orchestrator",
        "oracle",
        "librarian",
        "explorer",
        "designer",
        "fixer",
        "observer",
        "council",
    ):
        if (
            not isinstance(role_config.get(role), dict)
            or "model" not in role_config[role]
        ):
            raise FreePresetResolutionError(
                f"preset {preset!r} is missing role {role!r}"
            )
    for role in ("coder", "task_worker"):
        if role in role_config and (
            not isinstance(role_config[role], dict) or "model" not in role_config[role]
        ):
            raise FreePresetResolutionError(
                f"preset {preset!r} has invalid role {role!r}"
            )
    if not all(
        isinstance(choices.get(seat), dict) and "model" in choices[seat]
        for seat in ("alpha", "beta", "gamma", "council")
    ):
        raise FreePresetResolutionError(
            f"preset {preset!r} has incomplete council choices"
        )
    for role in (
        "orchestrator",
        "oracle",
        "librarian",
        "explorer",
        "designer",
        "fixer",
        "observer",
        "council",
    ):
        if not isinstance(fallback.get(role), list):
            raise FreePresetResolutionError(
                f"preset {preset!r} has incomplete fallback arrays"
            )
    _validate_model_paths(
        role_config,
        {
            "orchestrator",
            "oracle",
            "librarian",
            "explorer",
            "designer",
            "fixer",
            "observer",
            "council",
        },
        preset,
    )
    _validate_model_paths(choices, {"alpha", "beta", "gamma", "council"}, preset)
    _validate_model_paths(
        fallback,
        {
            "orchestrator",
            "oracle",
            "librarian",
            "explorer",
            "designer",
            "fixer",
            "observer",
            "council",
        },
        preset,
    )


def _validate_model_paths(value, allowed_keys, preset):
    for key, child in value.items():
        if key not in allowed_keys:
            if _contains_model(child):
                raise FreePresetResolutionError(
                    f"preset {preset!r} has unsupported model-bearing path {key!r}"
                )
            continue
        if isinstance(child, dict):
            for field, nested in child.items():
                if field != "model" and _contains_model(nested):
                    raise FreePresetResolutionError(
                        f"preset {preset!r} has unsupported nested model path {key}.{field}"
                    )
        elif isinstance(child, list) and any(_contains_model(item) for item in child):
            raise FreePresetResolutionError(
                f"preset {preset!r} has unsupported nested model path {key}"
            )


def _contains_model(value):
    if isinstance(value, dict):
        return "model" in value or any(
            _contains_model(child) for child in value.values()
        )
    if isinstance(value, list):
        return any(_contains_model(child) for child in value)
    return False


def materialize_free_presets(registry, candidates, presets=PRESETS, now=None):
    """Resolve and materialize requested free presets atomically in a deep copy."""
    if not isinstance(registry, dict):
        raise FreePresetResolutionError("registry must be an object")
    requested = tuple(dict.fromkeys(presets))
    if not requested or any(preset not in PRESETS for preset in requested):
        raise FreePresetResolutionError(
            "requested presets must be supported free presets"
        )
    candidates = tuple(candidates)
    for preset in requested:
        _require_schema(registry, preset)

    top_council = registry.get("council", {}).get("presets", {})
    for preset in requested:
        if preset in top_council:
            if not isinstance(top_council[preset], dict):
                raise FreePresetResolutionError(
                    f"preset {preset!r} has invalid top-level council schema"
                )
            _validate_model_paths(
                top_council[preset], {"alpha", "beta", "gamma"}, preset
            )
            if not all(
                isinstance(top_council[preset].get(seat), dict)
                and "model" in top_council[preset][seat]
                for seat in ("alpha", "beta", "gamma")
            ):
                raise FreePresetResolutionError(
                    f"preset {preset!r} has incomplete top-level council choices"
                )

    # Resolve every requested preset before changing even the returned copy.
    resolutions = {
        preset: resolve_free_preset(
            candidates, preset, _preferences(registry, preset, candidates), now=now
        )
        for preset in requested
    }
    result = copy.deepcopy(registry)
    audits = {}
    for preset, resolution in resolutions.items():
        if preset not in result.get("presets", {}) or preset not in result.get(
            "_tiers", {}
        ):
            raise FreePresetResolutionError(
                f"preset {preset!r} is missing required registry schema"
            )
        roles = result["presets"][preset]
        for role in (
            "orchestrator",
            "oracle",
            "librarian",
            "explorer",
            "designer",
            "fixer",
            "observer",
        ):
            roles[role]["model"] = _ref(resolution.role_choices[role])
        roles["council"]["model"] = _ref(resolution.role_choices["council-alpha"])

        tier = result["_tiers"][preset]
        council_choices = tier["council"]["presets"][preset]
        for role in COUNCIL_ROLES:
            seat = ROLE_NAMES[role]
            council_choices[seat]["model"] = _ref(resolution.role_choices[role])
        council_choices["council"]["model"] = _ref(
            resolution.role_choices["council-alpha"]
        )
        top_choices = result.get("council", {}).get("presets", {}).get(preset)
        if top_choices is not None:
            for role in COUNCIL_ROLES:
                top_choices[ROLE_NAMES[role]]["model"] = _ref(
                    resolution.role_choices[role]
                )

        for role in (
            "orchestrator",
            "oracle",
            "librarian",
            "explorer",
            "designer",
            "fixer",
            "observer",
            "council",
        ):
            resolver_role = "council-alpha" if role == "council" else role
            tier["fallback"][role] = [
                _ref(item) for item in resolution.fallback[resolver_role]
            ]
        audits[preset] = copy.deepcopy(resolution.audit)
    return result, audits
