#!/usr/bin/env python3
"""Generate and synchronize JetBrains model profiles from model-groups.json."""

from __future__ import annotations

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = SCRIPT_DIR if SCRIPT_DIR.endswith("lib") else os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)
import logger
from constants import MERIDIAN_DEFAULT_HOST, MERIDIAN_DEFAULT_PORT
from discover_models import list_local_ollama_models
from ai_models import resolve_model
from tier_resolve import resolve_roles_from_list


def build_provider_configs(cfg: dict) -> dict:
    """Build provider config dicts from model-groups.json provider entries.

    Override precedence: baseUrlEnv > hostEnvAlt > hostEnv/portEnv > baseUrl
    baseUrlEnv and hostEnvAlt keys are stripped from output (not understood by Junie).
    """
    provider_configs = {}

    for provider_name, provider_def in cfg.get("providers", {}).items():
        api_key_env = provider_def.get("apiKeyEnv", "")
        api_key = os.environ.get(api_key_env, "") if api_key_env else ""
        if api_key_env and not api_key:
            logger.warning(
                f"{api_key_env} not set — {provider_name} JetBrains AI profiles will have an empty apiKey"
            )

        hostEnvAlt = provider_def.get("hostEnvAlt", "")
        if hostEnvAlt:
            alt_value = os.environ.get(hostEnvAlt, "").strip()
            if alt_value:
                # hostEnvAlt includes scheme+host[:port] (e.g. http://__VG_IPV4_62de5b598c15__:11434)
                base_url = alt_value.rstrip("/")
                if not base_url.endswith("/v1"):
                    base_url = base_url + "/v1"
                # For meridian-style /responses suffix, check apiType
                if provider_def.get("apiType") == "OpenAIResponses":
                    base_url = base_url.replace("/v1", "/v1/responses")
            else:
                base_url = ""
        else:
            base_url = ""

        if not base_url:
            host_env = provider_def.get("hostEnv", "")
            port_env = provider_def.get("portEnv", "")
            if host_env or port_env:
                host = (
                    (os.environ.get(host_env) or MERIDIAN_DEFAULT_HOST)
                    if host_env
                    else MERIDIAN_DEFAULT_HOST
                )
                port = (
                    (os.environ.get(port_env) or MERIDIAN_DEFAULT_PORT)
                    if port_env
                    else MERIDIAN_DEFAULT_PORT
                )
                base_url = f"http://{host}:{port}/v1/responses"
            else:
                base_url = provider_def.get("baseUrl", "")

        baseUrlEnv = provider_def.get("baseUrlEnv", "")
        if baseUrlEnv and os.environ.get(baseUrlEnv, "").strip():
            base_url = os.environ.get(baseUrlEnv, "").strip().rstrip("/")

        provider_configs[provider_name] = {
            "baseUrl": base_url,
            "apiType": provider_def.get("apiType", ""),
            "apiKey": api_key,
        }

    return provider_configs


def resolve_local_placeholder(pattern: str, local_role_models: dict | None) -> str:
    if not pattern.startswith("_local:"):
        return pattern
    category = pattern[len("_local:") :]
    resolved = (local_role_models or {}).get(category, "")
    if resolved.startswith("ollama/"):
        return resolved[len("ollama/") :]
    return resolved or pattern


def resolve_group_model(
    provider_name: str,
    pattern: str,
    local_models: list,
    local_role_models: dict | None = None,
) -> str:
    if pattern.startswith("_local:"):
        return resolve_local_placeholder(pattern, local_role_models)
    if provider_name == "ollama":
        return resolve_model(pattern, local_models)
    return pattern


def lookup_model_temperature(model_name: str, overrides: dict) -> float | None:
    """Look up model-family temperature override by longest prefix match."""
    if not overrides:
        return None
    best_prefix = ""
    for prefix, temp in overrides.items():
        if model_name.lower().startswith(prefix.lower()) and len(prefix) > len(
            best_prefix
        ):
            best_prefix = prefix
    return overrides[best_prefix] if best_prefix else None


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

    provider_configs = build_provider_configs(cfg)

    local_models_list = args.local_models.split()
    local_role_models = {}
    has_local_groups = any(
        g.get("provider") == "ollama" for g in cfg.get("groups", {}).values()
    )
    if has_local_groups:
        discovered_local_models = list_local_ollama_models()
        if discovered_local_models:
            local_role_models = resolve_roles_from_list(discovered_local_models) or {}
            resolved_lines = [
                f"  {k} -> {v[len('ollama/') :] if v.startswith('ollama/') else v}"
                for k, v in sorted(local_role_models.items())
            ]
            logger.info(
                "Local model categories resolved:\n" + "\n".join(resolved_lines)
            )

    logger.info("Generating JetBrains AI model profiles...")

    groups = cfg.get("groups", {})
    model_temperatures = cfg.get("modelTemperatures", {})

    for group_name, group_def in groups.items():
        provider = group_def.get("provider")
        if not provider:
            logger.warning(f"No provider configured for {group_name} — skipping")
            continue

        if (
            provider == "github-copilot"
            and not os.environ.get("GITHUB_TOKEN", "").strip()
        ):
            logger.info(
                "GITHUB_TOKEN not set — skipping experimental %s group", group_name
            )
            continue

        provider_cfg = provider_configs.get(provider)
        if not provider_cfg:
            logger.warning(f"Unknown provider '{provider}' for {group_name} — skipping")
            continue

        primary_pattern = group_def.get("primaryModel", "")
        faster_pattern = group_def.get("fasterModel", "")
        faster_provider = group_def.get("fasterProvider") or provider

        if (
            provider == "ollama"
            and not local_models_list
            and not primary_pattern.startswith("_local:")
        ):
            logger.info(f"No local Ollama models available — skipping {group_name}")
            continue

        if (
            faster_pattern
            and faster_provider == "ollama"
            and not local_models_list
            and not faster_pattern.startswith("_local:")
        ):
            logger.info(f"No local Ollama models available — skipping {group_name}")
            continue

        if faster_provider not in provider_configs:
            logger.warning(
                f"Unknown faster provider '{faster_provider}' for {group_name} — skipping"
            )
            continue

        primary_id = resolve_group_model(
            provider, primary_pattern, local_models_list, local_role_models
        )
        if primary_pattern.startswith("_local:") and primary_id == primary_pattern:
            logger.warning(
                f"Could not resolve {primary_pattern} for {group_name} — skipping"
            )
            continue

        faster_id = (
            resolve_group_model(
                faster_provider, faster_pattern, local_models_list, local_role_models
            )
            if faster_pattern
            else ""
        )
        if faster_pattern.startswith("_local:") and faster_id == faster_pattern:
            logger.warning(
                f"Could not resolve {faster_pattern} for {group_name} — using primary as faster"
            )
            faster_id = primary_id

        faster_provider_cfg = provider_configs[faster_provider]

        primary_effective_temp = lookup_model_temperature(
            primary_id, model_temperatures
        )
        if primary_effective_temp is None:
            primary_effective_temp = 0.7

        profile_data = provider_cfg.copy()
        profile_data["id"] = primary_id
        profile_data["primaryModel"] = {
            "id": primary_id,
            "temperature": float(primary_effective_temp),
        }

        if faster_id:
            faster_effective_temp = lookup_model_temperature(
                faster_id, model_temperatures
            )
            if faster_effective_temp is None:
                faster_effective_temp = 0.7

            if faster_provider != provider:
                profile_data["fasterModel"] = {
                    "id": faster_id,
                    "baseUrl": faster_provider_cfg["baseUrl"],
                    "apiType": faster_provider_cfg["apiType"],
                    "apiKey": faster_provider_cfg["apiKey"],
                    "temperature": float(faster_effective_temp),
                }
            else:
                profile_data["fasterModel"] = {
                    "id": faster_id,
                    "temperature": float(faster_effective_temp),
                }

        profile_json = json.dumps(profile_data, indent=2) + "\n"
        profile_file = os.path.join(target_dir_path, f"{group_name}.json")
        fname = os.path.basename(profile_file)

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
