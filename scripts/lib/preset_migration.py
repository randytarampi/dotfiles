"""Compatibility migrations for preset and tier names."""

TIER_VALUE_MIGRATIONS = {
    "openai": "omo-slim-openai",
    "thirtydollars": "omo-slim-thirty-dollars",
    "opencode-zen-free": "omo-slim-opencode-zen-free",
}


def migrate_preset_value(value: str) -> str:
    """Return the current preset name for a legacy or current value."""
    return TIER_VALUE_MIGRATIONS.get(value, value)
