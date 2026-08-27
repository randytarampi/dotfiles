"""Shared tier registry access layer.

Treats configs/opencode/oh-my-opencode-slim.json ``presets`` as the canonical
tier -> role -> model registry.  Consumers use this module to resolve local
model placeholders consistently.
"""

import json
import os
from typing import Any, Dict, List, Optional

import logger


def load_registry(slim_path: Optional[str] = None) -> Dict[str, Any]:
    """Load and validate the registry.

    This performs file I/O; ``classify_models_for_preset`` may also invoke
    subprocesses through ``tier_resolve``.
    """
    if slim_path is None:
        slim_path = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__),
                "..",
                "..",
                "configs",
                "opencode",
                "oh-my-opencode-slim.json",
            )
        )
    with open(slim_path, "r", encoding="utf-8") as config_file:
        registry = json.load(config_file)
    for warning in validate_registry(registry):
        logger.warning(warning)
    return registry


def get_preset(registry: Dict, tier: str) -> Dict[str, Any]:
    return registry["presets"][tier]


def collect_placeholder_categories(value: Optional[str]) -> Optional[str]:
    if isinstance(value, str) and value.startswith("_local:"):
        return value[len("_local:") :]
    return None


def uses_local_placeholders(registry: Dict, tier: str) -> bool:
    return bool(get_preset_placeholder_categories(registry, tier))


def get_preset_placeholder_categories(registry: Dict, tier: str) -> set:
    categories = set()
    for role_config in get_preset(registry, tier).values():
        if isinstance(role_config, dict):
            category = collect_placeholder_categories(role_config.get("model"))
            if category:
                categories.add(category)
    return categories


def classify_models_for_preset(
    models: List[str],
    registry: Dict,
    tier: str,
    min_reasoning_embedding: int = 0,
) -> Dict[str, str]:
    from tier_resolve import resolve_roles_from_list

    return resolve_roles_from_list(
        models,
        min_reasoning_embedding=min_reasoning_embedding,
        moe_codegen_reuse=uses_local_placeholders(registry, tier),
    )


def apply_placeholder_overrides(
    category_models: Dict[str, str], overrides: List[str]
) -> None:
    """Apply category overrides in place; callers rely on this mutation."""
    for override in overrides or []:
        if "=" not in override:
            raise ValueError(
                f"Malformed placeholder override {override!r}; expected category=model"
            )
        category, model = override.split("=", 1)
        if not category or not model:
            raise ValueError(
                f"Malformed placeholder override {override!r}; expected category=model"
            )
        if category not in category_models:
            logger.warning("Unknown placeholder category override: %s", category)
        category_models[category] = model


def materialize_role_models(
    registry: Dict,
    tier: str,
    category_models: Dict[str, str],
    role_overrides: Optional[List[str]] = None,
) -> Dict[str, str]:
    role_models = {}
    for role, role_config in get_preset(registry, tier).items():
        if not isinstance(role_config, dict) or "model" not in role_config:
            continue
        model_ref = role_config["model"]
        category = collect_placeholder_categories(model_ref)
        if category:
            role_models[role] = category_models.get(category)
        else:
            role_models[role] = model_ref
    for override in role_overrides or []:
        if "=" not in override:
            raise ValueError(
                f"Malformed role override {override!r}; expected role=model"
            )
        role, model = override.split("=", 1)
        if not role or not model:
            raise ValueError(
                f"Malformed role override {override!r}; expected role=model"
            )
        if role not in role_models:
            logger.warning("Unknown role override: %s", role)
        role_models[role] = model
    return role_models


def validate_registry(registry: Dict) -> List[str]:
    warnings = []
    preset_keys = set(registry.get("presets", {}))
    tier_keys = set(registry.get("_tiers", {}))
    if preset_keys != tier_keys:
        warnings.append(
            "presets and _tiers tier keys differ: "
            f"presets-only={sorted(preset_keys - tier_keys)}, "
            f"_tiers-only={sorted(tier_keys - preset_keys)}"
        )
    return warnings
