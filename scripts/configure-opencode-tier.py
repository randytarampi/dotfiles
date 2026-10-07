#!/usr/bin/env python3
"""
OpenCode Tier Helper.
Handles local Ollama model role classification and placeholder resolution.
"""

import sys
import json
import importlib.util
import argparse
import shutil
import os
from typing import Optional

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = SCRIPT_DIR if SCRIPT_DIR.endswith("lib") else os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger  # noqa: E402 -- local imports follow the scripts/lib sys.path bootstrap.
from opencode_config import (  # noqa: E402 -- local import follows bootstrap.
    get_available_tiers,
)
from local_engines import (  # noqa: E402 -- local import follows bootstrap.
    engine_gate_active,
    resolve_engine,
)
from tier_resolve import (  # noqa: E402 -- local import follows bootstrap.
    list_local_ollama_models,
)
from cli_helpers import (  # noqa: E402 -- local import follows bootstrap.
    add_common_args,
    add_model_override_args,
)
from constants import get_litellm_proxy_mode  # noqa: E402
import tier_registry  # noqa: E402 -- local import follows bootstrap.


def _csv_env(name):
    return [
        value.strip() for value in os.environ.get(name, "").split(",") if value.strip()
    ]


def filter_omlx_models_for_gate(models):
    """Exclude oMLX entries when its opt-in gate is disabled."""
    if all(
        engine_gate_active(model.get("provider", ""))
        for model in models
        if isinstance(model, dict)
    ):
        return models
    return [
        model
        for model in models
        if not isinstance(model, dict)
        or not (
            (engine := resolve_engine(model.get("provider", "")))
            and engine.get("gate_required")
        )
    ]


def orchestrate_tier_switch(
    tier: str,
    no_local_fallbacks: bool,
    role_models: list,
    local_fallback_preset: Optional[str] = None,
    category_models: Optional[list] = None,
    min_reasoning_embedding: int = 0,
    dry_run: bool = False,
):
    use_litellm_proxy = get_litellm_proxy_mode()
    if use_litellm_proxy and os.environ.get("DOTFILES_RUN_LITELLM_SETUP", "0") != "1":
        logger.critical(
            "DOTFILES_USE_LITELLM_PROXY=1 requires DOTFILES_RUN_LITELLM_SETUP=1"
        )
        sys.exit(1)

    opencode_dir = os.environ.get("OPENCODE_DIR")
    if opencode_dir:
        config_dir = os.path.abspath(os.path.expanduser(opencode_dir))
    else:
        config_dir = os.path.join(os.path.expanduser("~"), ".config", "opencode")

    config_path = os.path.join(config_dir, "oh-my-opencode-slim.json")
    source_path = os.path.abspath(
        os.path.join(
            os.path.dirname(SCRIPT_DIR),
            "configs",
            "opencode",
            "oh-my-opencode-slim.json",
        )
    )

    if not os.path.exists(config_path):
        # Project mode: config_path may not exist yet. Copy from source.
        if os.path.exists(source_path):
            config_dir_path = os.path.dirname(config_path)
            if dry_run:
                logger.info(f"Would copy {source_path} → {config_path}")
            else:
                os.makedirs(config_dir_path, exist_ok=True)
                shutil.copy2(source_path, config_path)
                logger.info(f"Copied {source_path} → {config_path}")
        else:
            logger.critical(f"Config path does not exist: {config_path}")
            sys.exit(1)

    tiers_source = source_path if os.path.exists(source_path) else config_path
    if not os.path.exists(tiers_source):
        logger.critical(f"Source tiers file does not exist: {tiers_source}")
        sys.exit(1)

    try:
        registry = tier_registry.load_registry(tiers_source)
    except Exception as e:
        logger.critical(f"Failed to load tiers source {tiers_source}: {e}")
        sys.exit(1)

    tiers_dict = registry.get("_tiers", {})
    if tier not in tiers_dict:
        logger.critical(f"Tier '{tier}' not found in _tiers key of {tiers_source}")
        sys.exit(1)

    tier_config = tiers_dict[tier]
    preset = tier_registry.get_preset(registry, tier)
    resolution_preset = local_fallback_preset or (
        tier if tier_registry.uses_local_placeholders(registry, tier) else "local"
    )
    local_preset = tier_registry.uses_local_placeholders(registry, resolution_preset)

    local_role_models = {}
    if not no_local_fallbacks or local_preset:
        models_list = list_local_ollama_models()
        models_list = filter_omlx_models_for_gate(models_list)
        if models_list:
            local_role_models = tier_registry.classify_models_for_preset(
                models_list,
                registry,
                resolution_preset,
                min_reasoning_embedding,
            )

    tier_registry.apply_placeholder_overrides(local_role_models, category_models)
    fallback_role_models = local_role_models
    resolved_role_models = tier_registry.materialize_role_models(
        registry, tier, fallback_role_models, role_models
    )
    if not fallback_role_models:
        logger.warning("No local Ollama models discovered; skipping local fallbacks")
    else:
        logger.info(
            f"Classified local models: {json.dumps(local_role_models, indent=2)}"
        )
        # Log local role assignments (from the resolution preset's role mapping)
        if local_role_models:
            local_role_models_logged = tier_registry.materialize_role_models(
                registry, resolution_preset, local_role_models, role_models
            )
            local_assignments = []
            for role, model in sorted(local_role_models_logged.items()):
                if model:
                    local_assignments.append(f"  {role} → {model}")
            if local_assignments:
                logger.info("Local role assignments:\n" + "\n".join(local_assignments))
        # Log the active tier's role assignments (cloud for cloud tiers, local for local tiers)
        role_assignments = []
        for role, model in sorted(resolved_role_models.items()):
            if model:
                role_assignments.append(f"  {role} → {model}")
        if role_assignments:
            if local_preset:
                logger.info(
                    "Active tier role assignments:\n" + "\n".join(role_assignments)
                )
            else:
                logger.info("Cloud role assignments:\n" + "\n".join(role_assignments))
        if tier.startswith("local"):
            tier_council = tier_config.get("council", {})
            council_presets = tier_council.get("presets", {})
            if tier in council_presets:
                cp = council_presets[tier]
                councillor_models = []
                for role_key in ["alpha", "beta", "gamma"]:
                    placeholder = cp.get(role_key, {}).get("model", "")
                    category_key = placeholder.replace("_local:", "")
                    resolved = local_role_models.get(category_key, placeholder)
                    councillor_models.append(resolved)
                unique_councillors = set(councillor_models)
                if len(unique_councillors) < 3:
                    logger.warning(
                        f"Council has {len(unique_councillors)} distinct model(s) of 3 councillors — "
                        f"consider adding more diverse local models"
                    )

            needed_categories = sorted(
                tier_registry.get_preset_placeholder_categories(registry, tier)
            )
            sparse_categories = [
                category
                for category in needed_categories
                if len(
                    [
                        model
                        for role, model in local_role_models.items()
                        if role == category or role.startswith(f"{category}_")
                    ]
                )
                <= 1
            ]
            if sparse_categories:
                logger.warning(
                    f"Sparse local model categories for tier '{tier}': {', '.join(sparse_categories)}"
                )

            unique_models = set(fallback_role_models.values())
            logger.info(
                f"Local tier '{tier}': {len(unique_models)} distinct model(s) across "
                f"{len(fallback_role_models)} role categories"
            )
            # Show full role→model assignments for local tiers
            local_role_lines = []
            for role, model in sorted(resolved_role_models.items()):
                if model:
                    local_role_lines.append(f"  {role} → {model}")
            if local_role_lines:
                logger.info(
                    "Local tier role assignments:\n" + "\n".join(local_role_lines)
                )

    try:
        read_config_path = config_path if os.path.exists(config_path) else source_path
        with open(read_config_path, "r", encoding="utf-8") as f:
            target_config = json.load(f)
    except Exception as e:
        logger.critical(f"Failed to load target config {config_path}: {e}")
        sys.exit(1)

    target_config["preset"] = tier

    # Update preset role assignments from source presets
    source_presets_data = registry.get("presets", {})
    if tier in source_presets_data:
        source_preset = json.loads(json.dumps(preset))
        for role, model in resolved_role_models.items():
            if role in source_preset and isinstance(source_preset[role], dict):
                if model is None:
                    del source_preset[role]
                else:
                    source_preset[role]["model"] = model
        if "presets" not in target_config:
            target_config["presets"] = {}
        target_config["presets"][tier] = json.loads(json.dumps(source_preset))
        logger.info(f"Updated preset role assignments for '{tier}' from source")
    else:
        logger.warning(
            f"No preset '{tier}' found in source presets; role assignments not updated"
        )

    source_council = tier_config.get("council", {})
    if "council" not in target_config:
        target_config["council"] = {}
    target_config["council"]["default_preset"] = source_council.get("default_preset")
    source_presets = json.loads(json.dumps(source_council.get("presets", {}) or {}))

    for other_tier_name, other_tier_config in tiers_dict.items():
        other_council = other_tier_config.get("council", {})
        other_presets = other_council.get("presets", {})
        if other_tier_name in other_presets:
            other_preset = other_presets[other_tier_name]
            if "council" in other_preset and other_tier_name in source_presets:
                council_preset = other_preset["council"]
                source_presets[other_tier_name]["council"] = council_preset

    target_config["council"]["presets"] = source_presets

    if "_tiers" in target_config:
        del target_config["_tiers"]

    if fallback_role_models:
        logger.info("Resolving _local: model placeholders...")

        def resolve_value(value):
            if isinstance(value, str):
                category = tier_registry.collect_placeholder_categories(value)
                return fallback_role_models.get(category, value) if category else value
            if isinstance(value, dict):
                return {key: resolve_value(item) for key, item in value.items()}
            if isinstance(value, list):
                return [resolve_value(item) for item in value]
            return value

        target_config["council"]["presets"] = resolve_value(
            target_config["council"]["presets"]
        )
    else:
        logger.warning(
            "No local Ollama models found — _local: placeholders will not be resolved"
        )

    if use_litellm_proxy:
        if dry_run:
            logger.info("Would apply LiteLLM transport mapping to materialized tier")
        else:
            opencode_config_path = os.path.join(config_dir, "opencode.json")
            try:
                with open(opencode_config_path, encoding="utf-8") as f:
                    consumer_config = json.load(f)
                helper_path = os.path.join(SCRIPT_DIR, "configure-opencode.py")
                spec = importlib.util.spec_from_file_location(
                    "configure_opencode_transport", helper_path
                )
                if spec is None or spec.loader is None:
                    raise RuntimeError("Unable to load OpenCode transport helper")
                helper = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(helper)
                target_config = helper.apply_litellm_slim_gate(
                    target_config, consumer_config
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Failed to apply LiteLLM mapping to standalone tier output: {exc}"
                ) from exc

    try:
        if dry_run:
            logger.info(f"Would write tier configuration to {config_path}")
        else:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(target_config, f, indent=2)
                f.write("\n")
    except Exception as e:
        logger.critical(f"Failed to write configuration to {config_path}: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Switches the active OpenCode tier and updates configuration."
    )
    add_common_args(parser)
    parser.add_argument(
        "--no-local-fallbacks",
        action="store_true",
        help="Omit local Ollama models from fallback chains",
    )
    available_tiers = get_available_tiers()
    add_model_override_args(parser, preset_choices=available_tiers)
    parser.add_argument(
        "--min-reasoning-embedding",
        type=int,
        default=int(os.environ.get("DOTFILES_MIN_REASONING_EMBEDDING", "0") or "0"),
        help="Minimum embedding_length for reasoning/solo roles (0 = disabled). "
        "Env: DOTFILES_MIN_REASONING_EMBEDDING (default 0).",
    )
    parser.add_argument(
        "--preset",
        dest="preset",
        required=True,
        choices=available_tiers,
        help="Preferred interface: active OpenCode tier to set",
    )
    args = parser.parse_args()
    if args.role_models is None:
        args.role_models = _csv_env("DOTFILES_ROLE_MODELS")
        if not args.role_models and os.environ.get("DOTFILES_LOCAL_FALLBACK_ROLES"):
            logger.warning(
                "Deprecated DOTFILES_LOCAL_FALLBACK_ROLES; use DOTFILES_ROLE_MODELS instead"
            )
            args.role_models = _csv_env("DOTFILES_LOCAL_FALLBACK_ROLES")
    if args.category_models is None:
        args.category_models = _csv_env("DOTFILES_CATEGORY_MODELS")
        if not args.category_models and os.environ.get(
            "DOTFILES_LOCAL_FALLBACK_PLACEHOLDERS"
        ):
            logger.warning(
                "Deprecated DOTFILES_LOCAL_FALLBACK_PLACEHOLDERS; use DOTFILES_CATEGORY_MODELS instead"
            )
            args.category_models = _csv_env("DOTFILES_LOCAL_FALLBACK_PLACEHOLDERS")

    orchestrate_tier_switch(
        args.preset,
        args.no_local_fallbacks,
        args.role_models,
        local_fallback_preset=args.local_fallback_preset,
        category_models=args.category_models,
        min_reasoning_embedding=args.min_reasoning_embedding,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
