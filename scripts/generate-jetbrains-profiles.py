#!/usr/bin/env python3
"""Generate and synchronize JetBrains model profiles."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger
import tier_registry
from ai_models import resolve_model
from cli_helpers import add_local_fallback_args, add_min_reasoning_embedding_arg
from constants import (
    MERIDIAN_DEFAULT_HOST,
    MERIDIAN_DEFAULT_PORT,
    get_ollama_local_base_url,
)
from discover_models import list_local_ollama_models


def normalize_endpoint(base_url: str, api_type: str) -> str:
    """Return the full Junie endpoint for an OpenAI-compatible API type."""
    base_url = base_url.strip().rstrip("/")
    if re.search(r"/v\d+[A-Za-z0-9.-]+(?:/.*)?$", base_url):
        suffix = {
            "OpenAIResponses": "/responses",
            "OpenAICompletion": "/chat/completions",
        }.get(api_type)
        return f"{base_url}{suffix}" if suffix else base_url
    for suffix in ("/v1/responses", "/v1/chat/completions", "/v1"):
        if base_url.endswith(suffix):
            base_url = base_url[: -len(suffix)]
            break
    endpoint = {
        "OpenAIResponses": "/v1/responses",
        "OpenAICompletion": "/v1/chat/completions",
    }.get(api_type)
    return f"{base_url}{endpoint}" if endpoint else base_url


def build_provider_configs(cfg: dict) -> dict:
    provider_configs = {}
    for name, definition in cfg.get("providers", {}).items():
        key_env = definition.get("apiKeyEnv", "")
        api_key = os.environ.get(key_env, "") if key_env else ""
        host_alt = definition.get("hostEnvAlt", "")
        base_url = os.environ.get(host_alt, "").strip().rstrip("/") if host_alt else ""
        if not base_url and (definition.get("hostEnv") or definition.get("portEnv")):
            if name == "ollama":
                base_url = get_ollama_local_base_url()
            elif name == "meridian":
                host = (
                    os.environ.get(definition.get("hostEnv", ""))
                    or MERIDIAN_DEFAULT_HOST
                )
                port = (
                    os.environ.get(definition.get("portEnv", ""))
                    or MERIDIAN_DEFAULT_PORT
                )
                base_url = f"http://{host}:{port}/v1"
        if not base_url:
            base_url = definition.get("baseUrl", "")
        base_env = definition.get("baseUrlEnv", "")
        if base_env and os.environ.get(base_env, "").strip():
            base_url = os.environ[base_env].strip().rstrip("/")
        base_url = normalize_endpoint(base_url, definition.get("apiType", ""))
        provider_configs[name] = {
            "baseUrl": base_url,
            "apiType": definition.get("apiType", ""),
            "apiKey": api_key,
        }
    return provider_configs


def model_provider(model_ref: str) -> str:
    if model_ref.startswith("ollama-cloud/"):
        return "ollama-cloud"
    if model_ref.startswith("openai/"):
        return "openai"
    if model_ref.startswith("anthropic/"):
        return "meridian"
    if model_ref.startswith("ollama/"):
        return "ollama"
    return model_ref_provider(model_ref)


def model_ref_provider(model_ref: str) -> str:
    return model_ref.split("/", 1)[0] if "/" in model_ref else ""


def model_id(model_ref: str, provider_hint: str = "") -> str:
    if provider_hint and not model_ref.startswith(f"{provider_hint}/"):
        return model_ref
    return model_ref.split("/", 1)[1] if "/" in model_ref else model_ref


def lookup_model_temperature(model_name: str, overrides: dict) -> float | None:
    matches = [p for p in overrides if model_name.lower().startswith(p.lower())]
    return overrides[max(matches, key=len)] if matches else None


def main():
    parser = argparse.ArgumentParser(
        description="Generate JetBrains AI model profiles."
    )
    parser.add_argument(
        "--groups-json", required=True, help="Path to model-groups.json"
    )
    parser.add_argument("--target-dir", required=True, help="Path to ~/.junie/models")
    parser.add_argument(
        "--local-models", default="", help="Space-separated local Ollama models"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview without writing files"
    )
    add_local_fallback_args(parser)
    add_min_reasoning_embedding_arg(parser)
    args = parser.parse_args()

    groups_path = os.path.abspath(os.path.expanduser(args.groups_json))
    target_dir = os.path.abspath(os.path.expanduser(args.target_dir))
    if not os.path.isfile(groups_path):
        logger.critical(f"Groups file does not exist: {groups_path}")
        raise SystemExit(1)
    try:
        with open(groups_path, encoding="utf-8") as file:
            cfg = json.load(file)
        registry = tier_registry.load_registry()
    except Exception as exc:
        logger.critical(f"Failed to read model configuration: {exc}")
        raise SystemExit(1)

    local_models = args.local_models.split()
    if not local_models:
        local_models = [
            m["name"] if isinstance(m, dict) else str(m)
            for m in list_local_ollama_models()
        ]
    tier_specs = []
    for tier in registry.get("presets", {}):
        if not tier_registry.uses_local_placeholders(registry, tier):
            categories = {}
            roles = tier_registry.materialize_role_models(
                registry, tier, categories, args.local_fallback_role
            )
        else:
            tier_resolution_preset = args.local_fallback_preset or tier
            categories = {}
            if local_models:
                categories = tier_registry.classify_models_for_preset(
                    local_models,
                    registry,
                    tier_resolution_preset,
                    args.min_reasoning_embedding,
                )
                tier_registry.apply_placeholder_overrides(
                    categories, args.local_fallback_placeholder
                )
            roles = tier_registry.materialize_role_models(
                registry, tier, categories, args.local_fallback_role
            )
        orchestrator_ref = roles.get("orchestrator", "")
        librarian_ref = roles.get("librarian", "")
        tier_specs.append(
            (
                tier,
                orchestrator_ref,
                librarian_ref,
                None,
                None,
            )
        )
    if args.local_fallback_preset:
        logger.info(f"Using local fallback preset: {args.local_fallback_preset}")

    specs = tier_specs + [
        (
            name,
            group.get("primaryModel", ""),
            group.get("fasterModel", ""),
            group.get("provider"),
            group.get("fasterProvider"),
        )
        for name, group in cfg.get("groups", {}).items()
    ]
    providers = build_provider_configs(cfg)
    temperatures = cfg.get("modelTemperatures", {})
    generated = set()
    if not args.dry_run:
        os.makedirs(target_dir, exist_ok=True)
    logger.info("Generating JetBrains AI model profiles...")

    for name, primary_ref, faster_ref, explicit_provider, explicit_faster in specs:
        provider = explicit_provider or model_provider(primary_ref)
        faster_provider = explicit_faster or (
            model_provider(faster_ref) if faster_ref else provider
        )
        if not provider or provider not in providers:
            logger.warning(f"Unknown provider for {name} — skipping")
            continue
        if faster_provider not in providers:
            logger.warning(
                f"Unknown faster provider '{faster_provider}' for {name} — skipping"
            )
            continue
        if (
            provider == "github-copilot"
            and not os.environ.get("GITHUB_TOKEN", "").strip()
        ):
            logger.info(f"GITHUB_TOKEN not set — skipping experimental {name} group")
            continue
        if (
            provider == "ollama"
            and not local_models
            and not primary_ref.startswith("_local:")
        ):
            logger.info(f"No local Ollama models available — skipping {name}")
            continue
        primary = model_id(primary_ref, provider)
        if provider == "ollama" and not primary_ref.startswith("_local:"):
            primary = resolve_model(primary, local_models)
        if primary_ref.startswith("_local:") or not primary:
            logger.warning(f"Could not resolve primary model for {name} — skipping")
            continue
        faster = model_id(faster_ref, faster_provider) if faster_ref else ""
        if faster and faster_provider == "ollama":
            faster = resolve_model(faster, local_models)
        data = providers[provider].copy()
        data["id"] = primary
        primary_temperature = lookup_model_temperature(primary, temperatures)
        data["primaryModel"] = {
            "id": primary,
            "temperature": float(
                0.7 if primary_temperature is None else primary_temperature
            ),
        }
        if faster:
            faster_temperature = lookup_model_temperature(faster, temperatures)
            faster_data = {
                "id": faster,
                "temperature": float(
                    0.7 if faster_temperature is None else faster_temperature
                ),
            }
            if faster_provider != provider:
                faster_data.update(providers[faster_provider])
            data["fasterModel"] = faster_data
        output = json.dumps(data, indent=2) + "\n"
        path = os.path.join(target_dir, f"{name}.json")
        generated.add(os.path.basename(path))
        if args.dry_run:
            logger.info(
                f"Would configure: {name} → primary={primary} faster={faster or 'none'}"
            )
        else:
            existing_output = None
            if os.path.exists(path):
                with open(path, encoding="utf-8") as file:
                    existing_output = file.read()
            if existing_output == output:
                logger.info(f"Unchanged: {name} ({primary})")
                continue
            with open(path, "w", encoding="utf-8") as file:
                file.write(output)
            os.chmod(path, 0o644)
            logger.info(
                f"Configured: {name} → primary={primary} faster={faster or 'none'}"
            )

    if os.path.isdir(target_dir):
        for existing in os.listdir(target_dir):
            if existing.endswith(".json") and existing not in generated:
                if args.dry_run:
                    logger.info(f"Would remove stale: {existing}")
                else:
                    os.remove(os.path.join(target_dir, existing))
                    logger.info(f"Removed stale: {existing}")
    logger.info(f"JetBrains AI models configured at {target_dir}")
    if not args.dry_run:
        try:
            from model_stamp import write_stamp

            # Stamp marks last model sync for check-model-drift staleness warning.
            write_stamp()
        except Exception as exc:
            logger.warning(f"Could not write model sync stamp: {exc}")


if __name__ == "__main__":
    main()
