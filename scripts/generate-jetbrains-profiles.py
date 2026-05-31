#!/usr/bin/env python3
"""
Generate JetBrains AI Profiles Helper.
Generates, updates, and synchronizes JetBrains model profiles from model-groups.json.
"""

import sys
import json
import argparse
import os
import difflib

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = SCRIPT_DIR if SCRIPT_DIR.endswith("lib") else os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger


def resolve_model(pattern: str, available_models: list) -> str:
    # Exact match
    for m in available_models:
        if m == pattern:
            return m

    # Prefix match (longest name match)
    best = ""
    for m in available_models:
        if m.startswith(pattern):
            if not best or len(m) > len(best):
                best = m
    if best:
        return best

    # Fallback to pattern itself
    return pattern


def main():
    parser = argparse.ArgumentParser(
        description="Generates and manages JetBrains AI model profiles."
    )
    parser.add_argument(
        "--groups-json", required=True, help="Path to model-groups.json"
    )
    parser.add_argument("--target-dir", required=True, help="Path to ~/.junie/models")
    parser.add_argument(
        "--local-models", default="", help="Space-separated list of local Ollama models"
    )
    args = parser.parse_args()

    # Expand user paths
    groups_json_path = os.path.abspath(os.path.expanduser(args.groups_json))
    target_dir_path = os.path.abspath(os.path.expanduser(args.target_dir))

    if not os.path.exists(groups_json_path):
        logger.critical(f"Groups file does not exist: {groups_json_path}")
        sys.exit(1)

    try:
        with open(groups_json_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        logger.critical(f"Failed to read model groups: {e}")
        sys.exit(1)

    os.makedirs(target_dir_path, exist_ok=True)
    generated_files = set()

    # Base provider structures
    ollama_cloud_cfg = cfg.get("providers", {}).get("ollama-cloud", {}).copy()
    ollama_cloud_cfg.pop("apiKeyEnv", None)

    ollama_local_cfg = cfg.get("providers", {}).get("ollama-local", {})

    # Environment parameters
    ollama_api_key = os.environ.get("OLLAMA_API_KEY", "")
    if not ollama_api_key:
        logger.warning(
            "OLLAMA_API_KEY not set — Ollama Cloud JetBrains AI models will have empty apiKey"
        )

    meridian_host = os.environ.get("MERIDIAN_HOST", "127.0.0.1")
    meridian_port = os.environ.get("MERIDIAN_PORT", "3456")
    meridian_api_key = os.environ.get("MERIDIAN_API_KEY", "")
    meridian_cfg = {
        "baseUrl": f"http://{meridian_host}:{meridian_port}/v1/responses",
        "apiType": "OpenAIResponses",
        "apiKey": meridian_api_key,
    }

    local_models_list = args.local_models.split()

    logger.info("Generating JetBrains AI model profiles...")

    groups = cfg.get("groups", {})
    temperatures = cfg.get("temperatures", {})

    for group_name, group_def in groups.items():
        provider = group_def.get("provider")
        primary_pattern = group_def.get("primaryModel", "")
        faster_pattern = group_def.get("fasterModel", "")

        # Read temperature
        temp = group_def.get("temperature") or temperatures.get(group_name, 0.7)

        # Resolve provider and model IDs
        if provider == "ollama-local":
            provider_cfg = ollama_local_cfg.copy()
            if not local_models_list:
                logger.info(f"No local Ollama models available — skipping {group_name}")
                continue
            primary_id = resolve_model(primary_pattern, local_models_list)
            faster_id = (
                resolve_model(faster_pattern, local_models_list)
                if faster_pattern
                else ""
            )
        elif provider == "anthropic-meridian":
            provider_cfg = meridian_cfg.copy()
            primary_id = primary_pattern
            faster_id = faster_pattern
        else:
            provider_cfg = ollama_cloud_cfg.copy()
            provider_cfg["apiKey"] = ollama_api_key
            primary_id = primary_pattern
            faster_id = faster_pattern

        prefix = (
            "local"
            if provider == "ollama-local"
            else (
                "cloud-anthropic-meridian"
                if provider == "anthropic-meridian"
                else "cloud"
            )
        )

        # Construct Junie profile JSON
        profile_data = provider_cfg.copy()
        profile_data["id"] = primary_id
        profile_data["primaryModel"] = {"id": primary_id}
        if faster_id:
            profile_data["fasterModel"] = {"id": faster_id}
        if temp is not None:
            profile_data["temperature"] = float(temp)

        profile_json = json.dumps(profile_data, indent=2) + "\n"
        profile_file = os.path.join(target_dir_path, f"{prefix}-{group_name}.json")
        fname = os.path.basename(profile_file)

        # Compare with existing
        is_changed = True
        if os.path.exists(profile_file):
            with open(profile_file, "r", encoding="utf-8") as pf:
                existing_content = pf.read()
            if existing_content == profile_json:
                is_changed = False
                logger.info(f"Unchanged: {group_name} ({primary_id})")

        if is_changed:
            with open(profile_file, "w", encoding="utf-8") as pf:
                pf.write(profile_json)
            os.chmod(profile_file, 0o644)
            logger.info(
                f"Configured: {group_name} → primary={primary_id} faster={faster_id or 'none'}"
            )

        generated_files.add(fname)

    # Stale files cleanup
    for existing_file in os.listdir(target_dir_path):
        if existing_file.endswith(".json") and existing_file not in generated_files:
            full_stale_path = os.path.join(target_dir_path, existing_file)
            try:
                os.remove(full_stale_path)
                logger.info(f"Removed stale: {existing_file}")
            except Exception as e:
                logger.warning(f"Failed to remove stale file {existing_file}: {e}")

    logger.info(f"JetBrains AI models configured at {target_dir_path}")


if __name__ == "__main__":
    main()
