#!/usr/bin/env python3
"""
OpenCode Tier Helper.
Handles local Ollama model role classification and placeholder resolution.
"""

import sys
import json
import argparse
import subprocess
import shutil
import os
import re

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = SCRIPT_DIR if SCRIPT_DIR.endswith("lib") else os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger
from opencode_config import get_available_tiers, get_slim_config_path
from discover_models import find_ollama, parse_size_gb, list_local_ollama_models


def extract_param_count(model_name: str) -> int:
    """Extract parameter count from model name for size-based classification.

    Parses patterns like :27b, :480b, :1t from model tags.
    Returns 0 if no size pattern is found.
    """
    match = re.search(r":(\d+(?:\.\d+)?)([bmt])", model_name, re.IGNORECASE)
    if not match:
        return 0
    size = float(match.group(1))
    unit = match.group(2).lower()
    if unit == "t":
        return int(size * 1000)
    elif unit == "m":
        return max(1, int(size / 1000))
    else:
        return int(size)


def get_model_details(model_name: str) -> dict:
    """Run ollama show <model> and parse parameters/capabilities.

    Returns {"param_count": int, "capabilities": [str]}.
    Returns {"param_count": None, "capabilities": []} if ollama show fails or
    no parameters found.
    """
    ollama_bin = find_ollama()
    if not ollama_bin:
        return {"param_count": None, "capabilities": []}

    try:
        result = subprocess.run(
            [ollama_bin, "show", model_name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return {"param_count": None, "capabilities": []}

        param_count = None
        capabilities = []
        in_capabilities = False

        for line in result.stdout.splitlines():
            if not line.strip():
                in_capabilities = False
                continue

            if in_capabilities:
                if line[:1].isspace():
                    capability = line.strip()
                    if capability:
                        capabilities.append(capability)
                    continue
                in_capabilities = False

            if "parameters" in line:
                match = re.search(
                    r"parameters\s+([\d.]+)\s*([BMK])", line, re.IGNORECASE
                )
                if match:
                    value = float(match.group(1))
                    unit = match.group(2).upper()
                    if unit == "B":
                        param_count = int(value)
                    elif unit == "M":
                        param_count = int(value / 1000.0)
                    elif unit == "K":
                        param_count = int(value / 1000000.0)
                continue

            if line.strip().lower() == "capabilities":
                in_capabilities = True

        return {"param_count": param_count, "capabilities": capabilities}
    except Exception:
        return {"param_count": None, "capabilities": []}


def resolve_roles_from_list(models: list) -> dict:
    classified = {
        "reasoning": [],
        "code-gen": [],
        "_name_qualified_code_gen": [],
        "lightweight": [],
        "vision": [],
        "all": [],
    }
    model_details_cache = {}

    def get_cached_model_details(model_name: str) -> dict:
        if model_name not in model_details_cache:
            model_details_cache[model_name] = get_model_details(model_name)
        return model_details_cache[model_name]

    for model in models:
        if isinstance(model, dict):
            model_name = model.get("name", "")
            size_gb = float(model.get("size_gb", 0.0) or 0.0)
        else:
            model_name = str(model)
            size_gb = 0.0

        if not model_name:
            continue

        name_lower = model_name.lower()
        if any(
            p in name_lower
            for p in ["r1", "reasoning", "deep-think", "think", "qwq", "reflection"]
        ):
            category = "reasoning"
        elif any(
            p in name_lower
            for p in [
                "coder",
                "code",
                "coding",
                "devstral",
                "codestral",
                "laguna",
            ]
        ):
            category = "code-gen"
            classified["_name_qualified_code_gen"].append(
                {"name": model_name, "size_gb": size_gb}
            )
        elif any(p in name_lower for p in ["mini", "small", "tiny", "phi", "smol"]):
            category = "lightweight"
        elif size_gb < 12:
            category = "lightweight"
        else:
            category = "all"

        classified[category].append({"name": model_name, "size_gb": size_gb})

    remaining = classified["all"]
    if remaining:
        remaining_sorted = sorted(
            remaining,
            key=lambda m: extract_param_count(m["name"]),
            reverse=True,
        )
        large_models = []
        for model in remaining_sorted:
            details = get_cached_model_details(model["name"])
            param_count = details.get("param_count")
            if param_count is None:
                large_models.append(model)
                continue
            if param_count >= 7:
                classified["reasoning"].append(model)
            else:
                classified["code-gen"].append(model)

        if large_models:
            if len(large_models) >= 2:
                half = max(1, len(large_models) // 2)
                for m in large_models[:half]:
                    classified["reasoning"].append(m)
                for m in large_models[half:]:
                    classified["code-gen"].append(m)
            else:
                classified["reasoning"].extend(large_models)

    # Sort each category by descending size to prefer the most capable model
    def _effective_param_count(model):
        """Best-effort parameter count for sorting: name tag > ollama show > size heuristic."""
        name_count = extract_param_count(model.get("name", ""))
        if name_count > 0:
            return name_count
        return model.get("size_gb", 0.0)

    for cat in ["reasoning", "code-gen", "lightweight", "vision"]:
        # Enrich models without name-tag param counts via ollama show
        for model in classified[cat]:
            details = get_cached_model_details(model["name"])
            param_count = details.get("param_count")
            if param_count is not None:
                model["param_count"] = param_count
            model["capabilities"] = details.get("capabilities", [])

        def _sort_key(m):
            name_count = extract_param_count(m.get("name", ""))
            if name_count > 0:
                return name_count
            show_count = m.get("param_count")
            if show_count is not None and show_count > 0:
                return float(show_count)
            return m.get("size_gb", 0.0)

        classified[cat].sort(key=_sort_key, reverse=True)

    def _has_required_capabilities(model, required_caps):
        capabilities = model.get("capabilities", [])
        if not capabilities:
            return True
        return all(cap in capabilities for cap in required_caps)

    classified["reasoning"] = [
        m
        for m in classified["reasoning"]
        if _has_required_capabilities(m, ["thinking", "tools"])
    ]
    _code_gen_name_qualified = set()
    for m in classified.get("_name_qualified_code_gen", []):
        _code_gen_name_qualified.add(m["name"])
    classified["code-gen"] = [
        m
        for m in classified["code-gen"]
        if m["name"] in _code_gen_name_qualified
        or _has_required_capabilities(m, ["thinking", "completion"])
    ]
    classified["lightweight"] = [
        m for m in classified["lightweight"] if _has_required_capabilities(m, ["tools"])
    ]
    classified["vision"] = [
        m
        for m in classified["lightweight"]
        if _has_required_capabilities(m, ["vision"])
    ]
    classified["vision"].sort(key=lambda m: _effective_param_count(m), reverse=True)

    if not classified["vision"] and classified["lightweight"]:
        logger.warning(
            "No vision-capable local models found; falling back to lightweight"
        )
        classified["vision"] = classified["lightweight"][:]

    if not classified["code-gen"] and classified["reasoning"]:
        classified["code-gen"] = classified["reasoning"][:]

    def pick(category, fallback_category=None, index=0):
        """Pick the Nth model (0-based) from a category, with fallback."""
        src = classified.get(category, [])
        if index < len(src):
            return src[index]["name"]
        if fallback_category:
            src_fb = classified.get(fallback_category, [])
            if index < len(src_fb):
                return src_fb[index]["name"]
        # Final fallback: scan all categories for the Nth available model
        all_models = []
        for c in ["reasoning", "code-gen", "lightweight", "vision"]:
            all_models.extend(classified.get(c, []))
        if index < len(all_models):
            return all_models[index]["name"]
        return None

    role_map = {
        "reasoning": pick("reasoning", "code-gen"),
        "reasoning_2": pick("reasoning", "code-gen", index=1),
        "reasoning_3": pick("reasoning", "code-gen", index=2),
        "code-gen": pick("code-gen", "reasoning"),
        "code-gen_2": pick("code-gen", "reasoning", index=1),
        "lightweight": pick("lightweight", "code-gen"),
        "lightweight_2": pick("lightweight", "code-gen", index=1),
        "vision": pick("vision", "lightweight"),
    }

    resolved = {}
    for role, model in role_map.items():
        if model:
            resolved[role] = "ollama/" + model

    return resolved


def resolve_placeholders(config_path: str, local_roles_json: str):
    try:
        resolved = json.loads(local_roles_json)
    except Exception as e:
        logger.critical(f"Invalid local roles JSON: {e}")
        sys.exit(1)

    if not os.path.exists(config_path):
        logger.critical(f"Config path does not exist: {config_path}")
        sys.exit(1)

    # Process _3, _2 (longer) placeholders first to prevent partial replacement
    # e.g. _local:code-gen_3 must be replaced before _local:code-gen_2, then _local:code-gen
    placeholder_order = [
        ("_local:reasoning_3", resolved.get("reasoning_3")),
        ("_local:reasoning_2", resolved.get("reasoning_2")),
        ("_local:code-gen_2", resolved.get("code-gen_2")),
        ("_local:lightweight_2", resolved.get("lightweight_2")),
        ("_local:reasoning", resolved.get("reasoning")),
        ("_local:code-gen", resolved.get("code-gen")),
        ("_local:lightweight", resolved.get("lightweight")),
        ("_local:vision", resolved.get("vision")),
    ]

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()

        for placeholder, model in placeholder_order:
            if model:
                content = content.replace(placeholder, model)

        with open(config_path, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info("Local model placeholders resolved")
    except Exception as e:
        logger.critical(f"Failed to resolve placeholders: {e}")
        sys.exit(1)


def orchestrate_tier_switch(
    tier: str, no_local_fallbacks: bool, local_fallback_roles: list
):
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
        with open(tiers_source, "r", encoding="utf-8") as f:
            tiers_data = json.load(f)
    except Exception as e:
        logger.critical(f"Failed to load tiers source {tiers_source}: {e}")
        sys.exit(1)

    tiers_dict = tiers_data.get("_tiers", {})
    if tier not in tiers_dict:
        logger.critical(f"Tier '{tier}' not found in _tiers key of {tiers_source}")
        sys.exit(1)

    tier_config = tiers_dict[tier]

    role_to_local_category_path = os.path.abspath(
        os.path.join(
            os.path.dirname(SCRIPT_DIR),
            "configs",
            "opencode",
            "role-to-local-category.json",
        )
    )
    if os.path.exists(role_to_local_category_path):
        try:
            with open(role_to_local_category_path, "r", encoding="utf-8") as f:
                role_to_category = json.load(f)
        except Exception:
            role_to_category = {}
    else:
        role_to_category = {
            "orchestrator": "code-gen",
            "oracle": "reasoning",
            "librarian": "lightweight",
            "explorer": "lightweight",
            "fixer": "code-gen",
            "designer": "code-gen",
            "observer": "vision",
        }

    local_role_models = {}
    if not no_local_fallbacks or tier.startswith("local"):
        models_list = list_local_ollama_models()
        if models_list:
            local_role_models = resolve_roles_from_list(models_list)

    for override in local_fallback_roles:
        if "=" in override:
            role, model = override.split("=", 1)
            local_role_models[role] = model

    if not local_role_models:
        logger.warning("No local Ollama models discovered; skipping local fallbacks")
    else:
        logger.info(f"Classified local models: {json.dumps(local_role_models)}")
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

            needed_categories = sorted(set(role_to_category.values()))
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

            unique_models = set(local_role_models.values())
            logger.info(
                f"Local tier '{tier}': {len(unique_models)} distinct model(s) across "
                f"{len(local_role_models)} role categories"
            )

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            target_config = json.load(f)
    except Exception as e:
        logger.critical(f"Failed to load target config {config_path}: {e}")
        sys.exit(1)

    target_config["preset"] = tier

    # Update preset role assignments from source presets
    source_presets_data = tiers_data.get("presets", {})
    if tier in source_presets_data:
        source_preset = source_presets_data[tier]
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
                source_presets[other_tier_name]["council"] = other_preset["council"]

    target_config["council"]["presets"] = source_presets

    source_fallback = tier_config.get("fallback", {})
    if "fallback" not in target_config:
        target_config["fallback"] = {}
    target_config["fallback"]["chains"] = source_fallback

    if local_role_models and not tier.startswith("local"):
        updated_chains = {}
        for role, chain in source_fallback.items():
            category = role_to_category.get(role, "code-gen")
            local_model = local_role_models.get(category)
            if local_model and local_model not in chain:
                updated_chains[role] = chain + [local_model]
            else:
                updated_chains[role] = chain
        target_config["fallback"]["chains"] = updated_chains

    if "_tiers" in target_config:
        del target_config["_tiers"]

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(target_config, f, indent=2)
            f.write("\n")
    except Exception as e:
        logger.critical(f"Failed to write configuration to {config_path}: {e}")
        sys.exit(1)

    if local_role_models:
        logger.info("Resolving _local: model placeholders...")
        resolve_placeholders(config_path, json.dumps(local_role_models))
    else:
        logger.warning(
            "No local Ollama models found — _local: placeholders will not be resolved"
        )


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ["resolve-roles", "resolve-placeholders"]:
        parser = argparse.ArgumentParser(
            description="Helper script to handle OpenCode local model classification and placeholder resolution."
        )
        subparsers = parser.add_subparsers(dest="command", required=True)

        subparsers.add_parser(
            "resolve-roles",
            help="Reads model names from stdin and outputs role mapping JSON",
        )

        placeholder_parser = subparsers.add_parser(
            "resolve-placeholders",
            help="Resolves _local: placeholders in a config file",
        )
        placeholder_parser.add_argument(
            "--config-path",
            required=True,
            help="Path to the JSON config file to modify",
        )
        placeholder_parser.add_argument(
            "--local-roles",
            required=True,
            help="JSON string representing the resolved roles",
        )

        args = parser.parse_args()

        if args.command == "resolve-roles":
            stdin_models = []
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                    if isinstance(parsed, dict) and "name" in parsed:
                        stdin_models.append(
                            {
                                "name": parsed.get("name", ""),
                                "size_gb": float(parsed.get("size_gb", 0.0) or 0.0),
                            }
                        )
                        continue
                except Exception:
                    pass
                stdin_models.append({"name": line, "size_gb": 0.0})
            resolved = resolve_roles_from_list(stdin_models)
            print(json.dumps(resolved))
        elif args.command == "resolve-placeholders":
            resolve_placeholders(args.config_path, args.local_roles)
    else:
        parser = argparse.ArgumentParser(
            description="Switches the active OpenCode tier and updates configuration."
        )
        parser.add_argument(
            "--no-local-fallbacks",
            action="store_true",
            help="Omit local Ollama models from fallback chains",
        )
        parser.add_argument(
            "--local-fallback-role",
            action="append",
            default=[],
            help="Override local model for a role (e.g. observer=ollama/qwen3.5:9b-mlx)",
        )
        available_tiers = get_available_tiers()
        parser.add_argument(
            "--preset",
            dest="preset",
            choices=available_tiers,
            help="Active OpenCode tier to set (alias for positional TIER)",
        )
        parser.add_argument(
            "tier",
            nargs="?",
            choices=available_tiers,
            help="Active OpenCode tier to set",
        )
        args = parser.parse_args()

        # Merge --preset and positional tier; --preset takes priority
        resolved_tier = args.preset or args.tier
        if not resolved_tier:
            parser.error("the following arguments are required: tier (or --preset)")

        orchestrate_tier_switch(
            resolved_tier, args.no_local_fallbacks, args.local_fallback_role
        )


if __name__ == "__main__":
    main()
