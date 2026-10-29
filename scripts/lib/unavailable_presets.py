"""Shared denylist for presets whose routes are not currently available."""

DISABLED_PRESETS = frozenset({"free", "omo-slim-opencode-zen-free"})


def reject_disabled_preset(name):
    if name in DISABLED_PRESETS:
        raise ValueError(f"Preset '{name}' is unavailable")


def without_disabled_presets(registry):
    """Return a copy of Slim with disabled choices removed from every selector."""
    import copy

    result = copy.deepcopy(registry)
    if result.get("preset") in DISABLED_PRESETS:
        raise ValueError(f"Active default preset '{result['preset']}' is unavailable")
    council = result.get("council", {})
    if isinstance(council, dict):
        if council.get("default_preset") in DISABLED_PRESETS:
            raise ValueError(
                f"Active default council preset '{council['default_preset']}' is unavailable"
            )
        if isinstance(council.get("presets"), dict):
            council["presets"] = {
                name: value
                for name, value in council["presets"].items()
                if name not in DISABLED_PRESETS
            }
    for key in ("presets", "_tiers", "agents"):
        if isinstance(result.get(key), dict):
            result[key] = {
                name: value
                for name, value in result[key].items()
                if name not in DISABLED_PRESETS
            }
    for name, tier in result.get("_tiers", {}).items():
        council = tier.get("council", {}) if isinstance(tier, dict) else {}
        choices = council.get("presets") if isinstance(council, dict) else None
        if isinstance(choices, dict):
            council["presets"] = {
                name: value
                for name, value in choices.items()
                if name not in DISABLED_PRESETS
            }
        if (
            isinstance(council, dict)
            and council.get("default_preset") in DISABLED_PRESETS
        ):
            raise ValueError(
                f"Tier '{name}' selects unavailable council default "
                f"'{council['default_preset']}'"
            )
    return result
