#!/usr/bin/env python3
"""
OpenCode Tier Helper.
Handles local Ollama model role classification and placeholder resolution.
"""

import copy
import sys
import json
import importlib.util
import argparse
import os
import stat
from typing import Optional

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = SCRIPT_DIR if SCRIPT_DIR.endswith("lib") else os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from env import load_env  # noqa: E402 -- local import follows bootstrap.

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
from unavailable_presets import (  # noqa: E402 -- scripts/lib bootstrap above.
    DISABLED_PRESETS,
    reject_disabled_preset,
    without_disabled_presets,
)
from free_preset_registry import PRESETS as DYNAMIC_FREE_PRESETS  # noqa: E402
from free_preset_runtime import prepare_dynamic_free_registry  # noqa: E402

OPENCODE_CONFIG_FILENAME = "opencode.json"


def _csv_env(name):
    return [
        value.strip() for value in os.environ.get(name, "").split(",") if value.strip()
    ]


def _merge_dynamic_free_registry(target, resolved, selected):
    """Retain custom config while replacing stale dynamic-free selections."""
    result = copy.deepcopy(target)
    for name in DYNAMIC_FREE_PRESETS:
        result.get("presets", {}).pop(name, None)
        result.get("_tiers", {}).pop(name, None)
        result.get("council", {}).get("presets", {}).pop(name, None)
    result.setdefault("presets", {})[selected] = copy.deepcopy(
        resolved["presets"][selected]
    )
    result.setdefault("_tiers", {})[selected] = copy.deepcopy(
        resolved["_tiers"][selected]
    )
    council = result.setdefault("council", {})
    council["default_preset"] = resolved.get("council", {}).get("default_preset")
    selected_council = resolved.get("council", {}).get("presets", {}).get(selected)
    if selected_council is not None:
        council.setdefault("presets", {})[selected] = copy.deepcopy(selected_council)
    return result


def _dynamic_gateway_aliases(environ, audit):
    """Bind selected upstream identities to aliases advertised by LiteLLM."""
    from pathlib import Path
    from model_catalogues import get_catalogue

    port = environ.get("LITELLM_PORT", "4000")
    endpoint = f"http://127.0.0.1:{port}/v1"
    key_path = Path("~/.local/share/litellm/clients/opencode.key").expanduser()
    if (
        key_path.is_symlink()
        or not key_path.is_file()
        or stat.S_IMODE(key_path.stat().st_mode) != 0o600
        or key_path.stat().st_uid != os.getuid()
    ):
        raise ValueError("OpenCode LiteLLM key file is unavailable or unsafe")
    key = key_path.read_text(encoding="utf-8").strip()
    if not key:
        raise ValueError("OpenCode LiteLLM client key is empty")
    payload = get_catalogue(f"{endpoint}/models", key)
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise ValueError("LiteLLM /models response is malformed")
    aliases = [row.get("id") for row in data if isinstance(row, dict)]
    if len(aliases) != len(data) or any(
        not isinstance(alias, str) for alias in aliases
    ):
        raise ValueError("LiteLLM /models aliases are malformed")
    if len(aliases) != len(set(aliases)):
        raise ValueError("LiteLLM /models aliases are ambiguous")

    config_path = Path(
        os.path.join(
            environ.get("LITELLM_ROOT", "~/.local/share/litellm"), "config.yaml"
        )
    ).expanduser()
    manifest_path = config_path.with_name("free-route-manifest.json")
    if config_path.is_symlink() or manifest_path.is_symlink():
        raise ValueError("refusing symlinked LiteLLM evidence file")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    # The route projection comes from the trusted producer config, never from
    # the manifest. LiteLLM writes a credential-free companion route projection.
    projection_path = config_path.with_name("free-route-config.json")
    projection = json.loads(projection_path.read_text(encoding="utf-8"))
    routes = projection.get("routes") if isinstance(projection, dict) else None
    if not isinstance(routes, list):
        raise ValueError("trusted LiteLLM route projection is unavailable")
    if not all(isinstance(route, dict) for route in routes):
        raise ValueError("trusted LiteLLM route projection is malformed")
    if projection.get("config_binding_sha256") != manifest.get("config_binding_sha256"):
        raise ValueError("trusted route projection does not match manifest binding")
    from free_route_manifest import config_binding_digest

    config_projection = [
        {
            "model_name": row.get("model_name"),
            "litellm_params": row.get("litellm_params"),
        }
        for row in routes
    ]
    if config_binding_digest(config_projection) != manifest.get(
        "config_binding_sha256"
    ):
        raise ValueError("trusted route projection digest is invalid")
    for preset, chosen in audit["presets"].items():
        for role, serving_ref in chosen["selected"].items():
            if not isinstance(serving_ref, str) or serving_ref not in aliases:
                raise ValueError(f"no advertised LiteLLM alias for selected {role}")
    return {"manifest": manifest, "projection": projection, "aliases": aliases}


def load_effective_opencode_config(config_dir):
    """Merge managed JSON configs: global, project root, then config directory."""
    config_dir = os.path.realpath(os.path.expanduser(config_dir))
    global_dir = os.path.realpath(
        os.path.join(os.path.expanduser("~"), ".config", "opencode")
    )
    config_paths = [os.path.join(global_dir, OPENCODE_CONFIG_FILENAME)]
    if config_dir != global_dir:
        config_paths.append(
            os.path.join(os.path.dirname(config_dir), OPENCODE_CONFIG_FILENAME)
        )
        config_paths.append(os.path.join(config_dir, OPENCODE_CONFIG_FILENAME))

    def merge(target, overrides):
        """Recursively apply overrides to target, replacing non-dict values."""
        for key, value in overrides.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                merge(target[key], value)
            else:
                target[key] = value

    consumer_config = {}
    loaded = False
    for path in config_paths:
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"OpenCode config must be an object: {path}")
        merge(consumer_config, data)
        loaded = True
    if not loaded:
        raise FileNotFoundError(f"No OpenCode consumer config found for {config_dir}")
    return consumer_config


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
    """Materialize a tier with model overrides, local fallbacks and proxy routing.

    Write the Slim config under OPENCODE_DIR or the global config directory;
    dry runs log planned writes without changing files.
    """
    dynamic_free = tier in DYNAMIC_FREE_PRESETS
    try:
        if not dynamic_free:
            reject_disabled_preset(tier)
        if local_fallback_preset:
            reject_disabled_preset(local_fallback_preset)
        if dynamic_free and (role_models or category_models):
            raise ValueError("Model overrides are refused for dynamic free presets")
    except ValueError as error:
        logger.critical("%s", error)
        sys.exit(1)
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

    dynamic_source_registry = None
    dynamic_audit = None
    gateway_aliases = None
    if dynamic_free:
        if role_models:
            logger.critical(
                "Role model overrides are refused for dynamically resolved free tiers"
            )
            sys.exit(1)
        try:
            with open(source_path, encoding="utf-8") as source_file:
                source_registry = json.load(source_file)
            dynamic_source_registry, dynamic_audit = prepare_dynamic_free_registry(
                source_registry,
                [tier],
                os.environ,
            )
        except Exception as error:
            logger.critical(
                "Unable to resolve dynamic free tier before writes: %s", error
            )
            sys.exit(1)

    had_previous_slim = os.path.exists(config_path)
    if not had_previous_slim:
        # Project mode: create a sanitized copy before any discovery or selection.
        if os.path.exists(source_path):
            try:
                with open(source_path, encoding="utf-8") as source_file:
                    pristine_source = json.load(source_file)
                source_registry = (
                    dynamic_source_registry
                    if dynamic_source_registry is not None
                    else without_disabled_presets(pristine_source)
                )
            except (OSError, ValueError, UnicodeDecodeError) as error:
                logger.critical(
                    "Failed to load or sanitize Slim source %s: %s", source_path, error
                )
                sys.exit(1)
            config_dir_path = os.path.dirname(config_path)
            if dry_run:
                logger.info(f"Would write sanitized Slim config to {config_path}")
            else:
                os.makedirs(config_dir_path, exist_ok=True)
                with open(config_path, "w", encoding="utf-8") as target_file:
                    json.dump(source_registry, target_file, indent=2)
                    target_file.write("\n")
                logger.info(f"Wrote sanitized Slim config to {config_path}")
        else:
            logger.critical(f"Config path does not exist: {config_path}")
            sys.exit(1)

    tiers_source = source_path if os.path.exists(source_path) else config_path
    if not os.path.exists(tiers_source):
        logger.critical(f"Source tiers file does not exist: {tiers_source}")
        sys.exit(1)

    try:
        registry = tier_registry.load_registry(tiers_source)
        if dynamic_source_registry is not None:
            registry = copy.deepcopy(dynamic_source_registry)
        registry = (
            dynamic_source_registry
            if dynamic_source_registry is not None
            else without_disabled_presets(registry)
        )
    except Exception as e:
        logger.critical(f"Failed to load tiers source {tiers_source}: {e}")
        sys.exit(1)

    tiers_dict = registry.get("_tiers", {})
    if tier not in tiers_dict:
        logger.critical(f"Tier '{tier}' not found in _tiers key of {tiers_source}")
        sys.exit(1)

    tier_config = tiers_dict[tier]
    profile = tier_registry.get_profile_name(registry, tier)
    logger.info("Resolved tier profile for '%s': %s", tier, profile or "none")
    resolution_preset = local_fallback_preset or (
        tier if tier_registry.uses_local_placeholders(registry, tier) else "local"
    )
    local_preset = tier_registry.uses_local_placeholders(registry, resolution_preset)

    local_role_models = {}
    if (not no_local_fallbacks or local_preset) and not dynamic_free:
        models_list = list_local_ollama_models()
        models_list = filter_omlx_models_for_gate(models_list)
        if models_list:
            local_role_models = tier_registry.classify_models_for_preset(
                models_list,
                registry,
                resolution_preset,
                min_reasoning_embedding,
                profile=profile,
            )

    tier_registry.apply_placeholder_overrides(local_role_models, category_models)
    fallback_role_models = local_role_models
    resolved_role_models = tier_registry.materialize_role_models(
        registry, tier, fallback_role_models, role_models
    )
    if dynamic_free:
        if dynamic_audit is None:
            raise RuntimeError("Dynamic free tier audit is unavailable")
        resolved_role_models = dict(dynamic_audit["presets"][tier]["selected"])
        fallback_role_models = {}
        if use_litellm_proxy and not dry_run:
            gateway_aliases = _dynamic_gateway_aliases(os.environ, dynamic_audit)
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
        if not dynamic_free and tier_registry.uses_local_placeholders(registry, tier):
            tier_council = tier_config.get("council", {})
            council_presets = tier_council.get("presets", {})
            if tier in council_presets:
                cp = council_presets[tier] or {}
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
            loaded_target = json.load(f)
        previous_slim = copy.deepcopy(loaded_target) if had_previous_slim else None
        if dynamic_source_registry is not None:
            target_config = _merge_dynamic_free_registry(
                loaded_target, dynamic_source_registry, tier
            )
        else:
            target_config = without_disabled_presets(loaded_target)
    except Exception as e:
        logger.critical(f"Failed to load target config {config_path}: {e}")
        sys.exit(1)

    target_config["preset"] = tier

    # Update preset role assignments from source presets
    source_presets_data = registry.get("presets", {})
    if tier in source_presets_data:
        source_preset = json.loads(json.dumps(source_presets_data[tier]))
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
            if (
                isinstance(other_preset, dict)
                and "council" in other_preset
                and other_tier_name in source_presets
            ):
                council_preset = other_preset["council"]
                source_presets[other_tier_name]["council"] = council_preset

    target_config["council"]["presets"] = source_presets

    if dynamic_free:
        if dynamic_source_registry is None:
            raise RuntimeError("Dynamic free source registry is unavailable")
        dynamic_source = dynamic_source_registry["_tiers"][tier]
        target_config.setdefault("_tiers", {})[tier] = copy.deepcopy(dynamic_source)

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
            try:
                consumer_config = load_effective_opencode_config(config_dir)
                helper_path = os.path.join(SCRIPT_DIR, "configure-opencode.py")
                spec = importlib.util.spec_from_file_location(
                    "configure_opencode_transport", helper_path
                )
                if spec is None or spec.loader is None:
                    raise RuntimeError("Unable to load OpenCode transport helper")
                helper = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(helper)
                if gateway_aliases:
                    if dynamic_audit is None:
                        raise RuntimeError(
                            "Dynamic free tier gateway audit is unavailable"
                        )
                    selected = dynamic_audit["presets"][tier]["selected"]
                    for role, serving_ref in selected.items():
                        if (
                            not isinstance(serving_ref, str)
                            or serving_ref not in gateway_aliases["aliases"]
                        ):
                            raise ValueError(
                                f"no advertised LiteLLM alias for selected {role}"
                            )
                    target_config = helper.route_dynamic_free_registry(
                        target_config,
                        {"presets": {tier: {"selected": selected}}},
                        tier,
                        consumer_config,
                    )
                else:
                    target_config = helper.apply_litellm_slim_gate(
                        target_config, consumer_config, previous_slim
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
    available_tiers = [
        name
        for name in get_available_tiers()
        if name not in DISABLED_PRESETS or name in DYNAMIC_FREE_PRESETS
    ]
    add_model_override_args(parser, preset_choices=available_tiers)
    parser.add_argument(
        "--min-reasoning-embedding",
        type=int,
        default=None,
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
    load_env()
    if args.min_reasoning_embedding is None:
        try:
            args.min_reasoning_embedding = int(
                os.environ.get("DOTFILES_MIN_REASONING_EMBEDDING", "0") or "0"
            )
        except ValueError as error:
            parser.error(f"Invalid DOTFILES_MIN_REASONING_EMBEDDING value: {error}")
    args.local_fallback_preset = args.local_fallback_preset or os.environ.get(
        "DOTFILES_LOCAL_FALLBACK_PRESET"
    )
    if args.preset in DYNAMIC_FREE_PRESETS and (
        args.role_models or args.category_models or args.local_fallback_preset
    ):
        parser.error("Dynamic free presets refuse model and local fallback overrides")
    # Accept the deprecated tier names everywhere (choices include them via
    # get_available_tiers); resolve to canonical presets before use so
    # forwarded children never receive an alias.
    args.preset = tier_registry.resolve_deprecated_tier(args.preset)
    if args.local_fallback_preset:
        args.local_fallback_preset = tier_registry.resolve_deprecated_tier(
            args.local_fallback_preset
        )
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
