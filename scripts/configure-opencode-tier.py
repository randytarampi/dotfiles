#!/usr/bin/env python3
"""
OpenCode Tier Helper.
Handles local Ollama model role classification and placeholder resolution.
"""

import sys
import json
import argparse
import subprocess
import os
import re

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = SCRIPT_DIR if SCRIPT_DIR.endswith("lib") else os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger


def find_ollama() -> str:
    import shutil

    ollama_path = shutil.which("ollama")
    if ollama_path:
        return ollama_path
    try:
        result = subprocess.run(
            ["brew", "--prefix"], capture_output=True, text=True, timeout=5
        )
        brew_prefix = result.stdout.strip()
        if brew_prefix:
            candidate = os.path.join(brew_prefix, "bin", "ollama")
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    except Exception:
        pass
    return ""


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


def list_local_ollama_models() -> list:
    ollama_bin = find_ollama()
    if not ollama_bin:
        return []
    try:
        result = subprocess.run(
            [ollama_bin, "list"], capture_output=True, text=True, timeout=5
        )
        lines = result.stdout.splitlines()
        models = []
        for line in lines[1:]:
            parts = line.split()
            if parts:
                models.append(parts[0])
        return models
    except Exception:
        return []


def resolve_roles_from_list(models: list) -> dict:
    classified = {"reasoning": [], "code-gen": [], "lightweight": [], "all": []}

    for model in models:
        name_lower = model.lower()
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
                "deepseek-coder",
                "qwen2.5-coder",
                "qwen3-coder",
                "codeqwen",
            ]
        ):
            category = "code-gen"
        elif any(
            p in name_lower
            for p in ["mini", "small", "tiny", "phi", "gemma:2", "gemma3", "smol"]
        ):
            category = "lightweight"
        else:
            category = "all"

        classified[category].append(model)

    remaining = classified["all"]
    if remaining and len(remaining) >= 3:
        sorted_models = sorted(
            remaining, key=lambda m: extract_param_count(m), reverse=True
        )
        third = max(1, len(sorted_models) // 3)
        for m in sorted_models[:third]:
            classified["reasoning"].append(m)
        for m in sorted_models[third : 2 * third]:
            classified["code-gen"].append(m)
        for m in sorted_models[2 * third :]:
            classified["lightweight"].append(m)
    elif remaining:
        classified["code-gen"].extend(remaining)

    all_available = (
        classified["reasoning"] + classified["code-gen"] + classified["lightweight"]
    )
    if all_available:
        for cat in ["reasoning", "code-gen", "lightweight"]:
            if not classified[cat] and all_available:
                classified[cat] = [all_available[0]]

    def pick(category, fallback_category=None):
        if classified[category]:
            return classified[category][0]
        if fallback_category and classified[fallback_category]:
            return classified[fallback_category][0]
        for c in ["reasoning", "code-gen", "lightweight"]:
            if classified[c]:
                return classified[c][0]
        return None

    role_map = {
        "reasoning": pick("reasoning", "code-gen"),
        "code-gen": pick("code-gen", "reasoning"),
        "lightweight": pick("lightweight", "code-gen"),
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

    placeholder_map = {
        "_local:reasoning": resolved.get("reasoning"),
        "_local:code-gen": resolved.get("code-gen"),
        "_local:lightweight": resolved.get("lightweight"),
    }

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()

        for placeholder, model in placeholder_map.items():
            if model:
                content = content.replace(placeholder, model)

        with open(config_path, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info("Local model placeholders resolved")
    except Exception as e:
        logger.critical(f"Failed to resolve placeholders: {e}")
        sys.exit(1)


def orchestrate_tier_switch(tier: str, with_local_fallbacks: bool):
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
            "observer": "lightweight",
        }

    local_role_models = {}
    if with_local_fallbacks or tier == "local":
        models_list = list_local_ollama_models()
        if models_list:
            local_role_models = resolve_roles_from_list(models_list)

        if not local_role_models:
            logger.warning(
                "No local Ollama models discovered; skipping local fallbacks"
            )
        else:
            logger.info(f"Classified local models: {json.dumps(local_role_models)}")

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            target_config = json.load(f)
    except Exception as e:
        logger.critical(f"Failed to load target config {config_path}: {e}")
        sys.exit(1)

    target_config["preset"] = tier

    source_council = tier_config.get("council", {})
    if "council" not in target_config:
        target_config["council"] = {}
    target_config["council"]["master"] = source_council.get("master")
    target_config["council"]["default_preset"] = source_council.get("default_preset")
    target_config["council"]["presets"] = source_council.get("presets")

    source_fallback = tier_config.get("fallback", {})
    if "fallback" not in target_config:
        target_config["fallback"] = {}
    target_config["fallback"]["chains"] = source_fallback

    if local_role_models and tier != "local":
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

    if tier == "local":
        if not local_role_models:
            logger.warning(
                "No local Ollama models found — _local: placeholders will not be resolved"
            )
        else:
            logger.info("Resolving _local: model placeholders for local tier...")
            resolve_placeholders(config_path, json.dumps(local_role_models))


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
            stdin_models = [line.strip() for line in sys.stdin if line.strip()]
            resolved = resolve_roles_from_list(stdin_models)
            print(json.dumps(resolved))
        elif args.command == "resolve-placeholders":
            resolve_placeholders(args.config_path, args.local_roles)
    else:
        parser = argparse.ArgumentParser(
            description="Switches the active OpenCode tier and updates configuration."
        )
        parser.add_argument(
            "--with-local-fallbacks",
            action="store_true",
            help="Optionally appends local Ollama models to fallback chains",
        )
        parser.add_argument(
            "tier",
            choices=[
                "pro",
                "pro-plus",
                "pro-plus-anthropic",
                "plus",
                "plus-anthropic",
                "anthropic",
                "local",
            ],
            help="Active OpenCode tier to set",
        )
        args = parser.parse_args()

        orchestrate_tier_switch(args.tier, args.with_local_fallbacks)


if __name__ == "__main__":
    main()
