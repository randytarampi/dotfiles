#!/usr/bin/env python3
"""
OpenCode Voice Plugin Configurator.

Generates ~/.config/opencode/tui.json with tier-aware voice plugin configuration.
Supports:
  - Local Ollama LLM + whisper-cli STT (pro, local, pro-plus fallback)
  - Ollama Cloud LLM + whisper-cli STT (pro, pro-plus)
  - OpenAI LLM + OpenAI STT (plus, plus-anthropic)
  - Anthropic/Meridian LLM + OpenAI STT (anthropic, pro-plus-anthropic)

Usage:
  configure-opencode-voice.py [--preset TIER] [--dry-run] [--show-secrets] [--no-backup]

Environment variables:
  DOTFILES_USE_LOCAL_OLLAMA  - Include local Ollama endpoint (default: true)
  OPENCODE_DIR               - Override config directory
  MERIDIAN_API_KEY           - Meridian proxy API key
  MERIDIAN_HOST              - Meridian proxy host (default: 127.0.0.1)
  MERIDIAN_PORT              - Meridian proxy port (default: 3456)
  ANTHROPIC_API_KEY          - Anthropic API key (direct)
  ANTHROPIC_BASE_URL         - Anthropic base URL (indicates Meridian proxy)
  OPENAI_API_KEY             - OpenAI API key (for cloud STT)
  OLLAMA_API_KEY             - Ollama Cloud API key
  DOTFILES_WHISPER_MODEL    - Whisper model filename (default: ggml-large-v3-turbo.bin)
  DOTFILES_PIPER_VOICE       - Piper voice name (default: en_US-lessac-high)
"""

import sys
import json
import argparse
import os

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
LIB_DIR = SCRIPT_DIR if SCRIPT_DIR.endswith("lib") else os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger
from constants import (
    get_meridian_base_url,
    get_ollama_local_base_url,
    check_ollama_daemon,
    get_provider_base_url,
    is_meridian_configured,
)
from file_utils import backup_file, write_text_file
from env import load_env
from ai_models import strip_provider_prefix
from opencode_config import get_available_tiers
from cli_helpers import add_model_override_args, add_min_reasoning_embedding_arg
from tier_resolve import (
    get_model_details,
    list_local_ollama_models,
    resolve_roles_from_list,
)
from provider_endpoints import PROVIDER_ENDPOINTS
from local_engines import (
    active_engines,
    audio_models,
    iter_engine_discovery,
    local_endpoint_for,
    resolve_engine,
)
import tier_registry


def local_voice_provider(model_ref):
    """Return the provider-aware voice config for a local model reference."""
    provider = model_ref.split("/", 1)[0] if "/" in model_ref else "ollama"
    endpoint = local_endpoint_for(provider, "openai")
    if endpoint is None:
        provider = "ollama"
        endpoint = local_endpoint_for(provider, "openai")
    base_url, api_key_env = endpoint
    config = {"endpoint": base_url, "model": strip_provider_prefix(model_ref)}
    if api_key_env and os.environ.get(api_key_env, "").strip():
        config["apiKeyEnv"] = api_key_env
    return config


def _voice_resolution_options(
    local_fallback_preset,
    role_models,
    category_models,
    min_reasoning_embedding,
    stt_transport,
):
    return {
        "local_fallback_preset": local_fallback_preset,
        "role_models": role_models,
        "category_models": category_models,
        "min_reasoning_embedding": min_reasoning_embedding,
        "stt_transport": stt_transport,
    }


def add_voice_transport_arguments(parser):
    """Keep the voice CLI's supported transport options explicit."""
    parser.add_argument(
        "--stt-transport",
        choices=("auto", "local", "openai"),
        default="auto",
        help="STT transport policy (auto preserves tier-based behaviour)",
    )


def get_voice_config(
    tier: str,
    options=None,
) -> dict:
    """Build the opencode-voice plugin config dict based on tier.

    Returns a dict suitable for the plugin entry in tui.json:
      {"endpoint": ..., "model": ..., optional "apiKeyEnv": ...,
       optional "sttEndpoint": ..., optional "sttModel": ..., optional "sttApiKeyEnv": ...}
    """
    options = options or {}
    local_fallback_preset = options.get("local_fallback_preset")
    role_models = options.get("role_models")
    category_models = options.get("category_models")
    min_reasoning_embedding = options.get("min_reasoning_embedding")
    stt_transport = options.get("stt_transport", "auto")
    is_anthropic_tier = tier in ("anthropic", "pro-plus-anthropic")
    is_plus_tier = tier in (
        "plus",
        "plus-anthropic",
        "omo-slim-openai",
        "omo-slim-thirty-dollars",
    )
    is_local_tier = tier.startswith("local")
    is_pro_tier = tier == "pro"
    unresolved = False
    no_local_models = False
    registry = tier_registry.load_registry()
    resolution_preset = local_fallback_preset or (
        tier if tier_registry.uses_local_placeholders(registry, tier) else "local"
    )

    # Determine Meridian proxy usage for Anthropic tiers
    use_meridian = is_meridian_configured()
    # Check if local Ollama daemon can proxy cloud models
    _, can_proxy_cloud = check_ollama_daemon()

    # ── LLM endpoint ──────────────────────────────────────────────────
    if is_local_tier:
        # Local Ollama — no API key needed
        voice_config = {
            "endpoint": get_ollama_local_base_url(),
        }
        # Pick the best local model for voice
        use_local_ollama = os.environ.get(
            "DOTFILES_USE_LOCAL_OLLAMA", "true"
        ).lower() in (
            "true",
            "1",
        )
        if use_local_ollama:
            try:
                discovery = iter_engine_discovery("ollama")
                local_models = list(discovery.records)
                if discovery.status == "unknown_error":
                    unresolved = True
                    logger.warning(
                        "Local voice discovery unavailable; preserving existing voice selection"
                    )
                elif discovery.status == "ok_empty":
                    unresolved = True
                    no_local_models = True
                    logger.warning(
                        "No local voice models discovered; voice configuration is unresolved"
                    )
                if unresolved:
                    local_models = []
                if local_models:
                    min_emb = min_reasoning_embedding
                    if min_emb is None:
                        min_emb = int(
                            os.environ.get("DOTFILES_MIN_REASONING_EMBEDDING", "0")
                            or "0"
                        )
                    categories = resolve_roles_from_list(
                        local_models,
                        min_reasoning_embedding=min_emb,
                        moe_codegen_reuse=False,
                    )
                    tier_registry.apply_placeholder_overrides(
                        categories, category_models
                    )
                    resolved_roles = tier_registry.materialize_role_models(
                        registry, resolution_preset, categories, role_models
                    )

                    def _voice_llm_capable(model_ref):
                        """Require verified completion capability for the voice LLM."""
                        provider, _, name = model_ref.partition("/")
                        engine_provider = (
                            provider if provider in ("ollama", "omlx") else "ollama"
                        )
                        try:
                            details = get_model_details(name, engine_provider)
                        except (OSError, ValueError, RuntimeError) as error:
                            logger.warning(
                                "Voice model capability lookup failed for %s: %s",
                                model_ref,
                                error,
                            )
                            return False
                        caps = details.get("capabilities") or []
                        if "completion" in caps:
                            return True
                        logger.info(
                            "Voice LLM: skipping STT-only audio winner %s "
                            "(no completion capability)",
                            model_ref,
                        )
                        return False

                    candidates = [
                        (
                            categories.get("solo")
                            if resolution_preset == "local-solo"
                            else categories.get("audio")
                        ),
                        categories.get("lightweight"),
                        resolved_roles.get("librarian"),
                    ]
                    verified_candidates = [
                        candidate
                        for candidate in dict.fromkeys(c for c in candidates if c)
                        if _voice_llm_capable(candidate)
                    ]
                    voice_model = next(iter(verified_candidates), None)
                    if voice_model:
                        voice_config = local_voice_provider(voice_model)
                    else:
                        unresolved = True
                        logger.warning(
                            "Local voice models lack verified completion capability; voice configuration is unresolved"
                        )
            except (OSError, ValueError, RuntimeError) as error:
                logger.warning("Local voice model resolution failed: %s", error)
                unresolved = True
        if "model" not in voice_config and not unresolved:
            raise RuntimeError("Could not resolve a verified local voice LLM")

    elif is_pro_tier:
        # Ollama Cloud — route through local daemon if cloud-capable
        if can_proxy_cloud:
            voice_config = {
                "endpoint": get_ollama_local_base_url(),
                "model": "gemma4:31b:cloud",
            }
        else:
            voice_config = {
                "endpoint": get_provider_base_url("ollama-cloud"),
                "model": "gemma4:31b",
                "apiKeyEnv": "OLLAMA_API_KEY",
            }

    elif tier in ("omo-slim-opencode-zen-free", "free"):
        # These tiers use configured remote providers, never Ollama.
        provider = PROVIDER_ENDPOINTS["opencode"]
        orchestrator = tier_registry.materialize_role_models(
            registry, tier, {}, None
        ).get("orchestrator", "opencode/muse-spark-1.3-contributor-free")
        voice_config = {
            "endpoint": provider["baseUrl"],
            "model": strip_provider_prefix(orchestrator),
            "apiKeyEnv": provider["apiKeyEnv"],
        }

    elif is_anthropic_tier:
        # Anthropic — via Meridian proxy or direct
        if use_meridian:
            voice_config = {
                "endpoint": get_meridian_base_url(),
                "model": "claude-sonnet-5-5",
                "apiKeyEnv": "MERIDIAN_API_KEY",
            }
        else:
            voice_config = {
                "endpoint": get_provider_base_url("anthropic"),
                "model": "claude-sonnet-5-5",
                "apiKeyEnv": "ANTHROPIC_API_KEY",
            }

    elif is_plus_tier or tier == "pro-plus":
        # OpenAI or Ollama Cloud + OpenAI STT
        voice_config = {
            "endpoint": get_provider_base_url("openai"),
            "model": "gpt-6-luna",
            "apiKeyEnv": "OPENAI_API_KEY",
        }

    else:
        # Fallback: Ollama Cloud
        if can_proxy_cloud:
            voice_config = {
                "endpoint": get_ollama_local_base_url(),
                "model": "gemma4:31b:cloud",
            }
        else:
            voice_config = {
                "endpoint": get_provider_base_url("ollama-cloud"),
                "model": "gemma4:31b",
                "apiKeyEnv": "OLLAMA_API_KEY",
            }

    if (
        not is_local_tier
        and tier not in ("omo-slim-opencode-zen-free", "free")
        and os.environ.get("DOTFILES_USE_LOCAL_OLLAMA", "true").lower() in ("true", "1")
    ):
        try:
            local_models = list_local_ollama_models()
            if local_models:
                min_emb = min_reasoning_embedding
                if min_emb is None:
                    min_emb = int(
                        os.environ.get("DOTFILES_MIN_REASONING_EMBEDDING", "0") or "0"
                    )
                categories = resolve_roles_from_list(
                    local_models,
                    min_reasoning_embedding=min_emb,
                    moe_codegen_reuse=False,
                )
                tier_registry.apply_placeholder_overrides(categories, category_models)
                resolved_roles = tier_registry.materialize_role_models(
                    registry, resolution_preset, categories, role_models
                )
                voice_model = resolved_roles.get("librarian")
                if voice_model:
                    voice_config = local_voice_provider(voice_model)
        except (OSError, ValueError, RuntimeError, KeyError) as error:
            logger.warning(
                "Local voice model resolution failed; retaining configured tier model: %s",
                error,
            )

    # ── STT config ──────────────────────────────────────────────────
    # Cloud tiers use OpenAI Whisper for STT when OPENAI_API_KEY is available.
    # Local/Ollama Cloud tiers use whisper-cli (local).
    # plus/plus-anthropic use OpenAI STT (bundled with their OpenAI LLM endpoint).

    has_openai_key = bool(os.environ.get("OPENAI_API_KEY", ""))

    def configure_local_stt():
        for provider in active_engines():
            engine = resolve_engine(provider)
            opt_out_env = engine.get("audio_opt_out_env") if engine else None
            if opt_out_env and os.environ.get(opt_out_env, "true").lower() not in (
                "true",
                "1",
            ):
                continue
            models = [
                m for m in audio_models(provider) if m.get("model_type") == "audio_stt"
            ]
            endpoint = local_endpoint_for(provider, "openai")
            if not models or endpoint is None:
                continue
            voice_config["sttEndpoint"] = endpoint[0]
            voice_config["sttModel"] = strip_provider_prefix(models[0]["name"])
            if endpoint[1] and os.environ.get(endpoint[1], "").strip():
                voice_config["sttApiKeyEnv"] = endpoint[1]
            return True
        return False

    if stt_transport == "local":
        configure_local_stt()
    elif stt_transport == "openai":
        if not has_openai_key:
            raise RuntimeError(
                "OpenAI STT transport requested but OPENAI_API_KEY is unavailable"
            )
        voice_config["sttEndpoint"] = get_provider_base_url("openai")
        voice_config["sttModel"] = "whisper-1"
        voice_config["sttApiKeyEnv"] = "OPENAI_API_KEY"
    elif stt_transport == "auto" and is_plus_tier:
        # OpenAI tiers — use OpenAI for STT too
        voice_config["sttEndpoint"] = get_provider_base_url("openai")
        voice_config["sttModel"] = "whisper-1"
        voice_config["sttApiKeyEnv"] = "OPENAI_API_KEY"
    elif stt_transport == "auto" and is_anthropic_tier and has_openai_key:
        # Anthropic tiers — upgrade STT to OpenAI if key available
        voice_config["sttEndpoint"] = get_provider_base_url("openai")
        voice_config["sttModel"] = "whisper-1"
        voice_config["sttApiKeyEnv"] = "OPENAI_API_KEY"
    elif stt_transport == "auto" and tier == "pro-plus" and has_openai_key:
        # pro-plus — upgrade STT to OpenAI if key available
        voice_config["sttEndpoint"] = get_provider_base_url("openai")
        voice_config["sttModel"] = "whisper-1"
        voice_config["sttApiKeyEnv"] = "OPENAI_API_KEY"
    elif stt_transport == "auto" and tier == "pro-plus-anthropic" and has_openai_key:
        # pro-plus-anthropic — upgrade STT to OpenAI if key available
        voice_config["sttEndpoint"] = get_provider_base_url("openai")
        voice_config["sttModel"] = "whisper-1"
        voice_config["sttApiKeyEnv"] = "OPENAI_API_KEY"
    elif stt_transport == "auto" and configure_local_stt():
        pass
    # else: local/pro use whisper-cli (no stt config needed — plugin auto-detects)

    # This plugin synthesizes through Piper; HTTP TTS belongs to Open WebUI.
    discovered_tts = any(
        model.get("model_type") == "audio_tts" for model in audio_models("omlx")
    )
    logger.warning(
        "The installed opencode-voice consumer (<=0.6.0) synthesizes via local "
        "Piper and ignores HTTP TTS configuration; local HTTP TTS is served "
        "through the Open WebUI audio bundle%s",
        " (oMLX TTS model discovered)" if discovered_tts else "",
    )

    # ── Optional custom prompt files ─────────────────────────────────
    # These are optional — only include if the files exist
    opencode_dir = os.environ.get("OPENCODE_DIR")
    if opencode_dir:
        config_dir = os.path.abspath(os.path.expanduser(opencode_dir))
    else:
        config_dir = os.path.join(os.path.expanduser("~"), ".config", "opencode")

    stt_prompt = os.path.join(config_dir, "stt-prompt.md")
    if os.path.exists(stt_prompt):
        voice_config["sttPrompt"] = stt_prompt

    tts_auto_prompt = os.path.join(config_dir, "tts-auto-prompt.md")
    if os.path.exists(tts_auto_prompt):
        voice_config["ttsAutoPrompt"] = tts_auto_prompt

    tts_manual_prompt = os.path.join(config_dir, "tts-manual-prompt.md")
    if os.path.exists(tts_manual_prompt):
        voice_config["ttsManualPrompt"] = tts_manual_prompt

    # STT/TTS remain engine-local and independent of the voice LLM pool
    # (Workstream E): an unresolved LLM selection must not discard a healthy
    # engine's resolved STT/TTS configuration.
    if (
        unresolved
        and (no_local_models or is_local_tier)
        and not voice_config.get("sttEndpoint")
    ):
        logger.warning(
            "Voice configuration unresolved: %s",
            (
                "no local pools discovered"
                if no_local_models
                else "no verified candidates"
            ),
        )
        return {"_voice_unresolved": True}
    return voice_config


def main():
    parser = argparse.ArgumentParser(
        description="Configure OpenCode voice plugin (tui.json) based on active tier."
    )
    available_tiers = get_available_tiers()
    add_model_override_args(parser)
    parser.add_argument(
        "--preset",
        default="pro-plus",
        choices=available_tiers,
        help="Active OpenCode tier (determines voice LLM/STT endpoints)",
    )
    add_voice_transport_arguments(parser)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print config to stdout without writing files",
    )
    parser.add_argument(
        "--show-secrets",
        action="store_true",
        help="Include API key env var names in dry-run output (keys are never printed)",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip backup of existing tui.json",
    )
    add_min_reasoning_embedding_arg(parser)
    args = parser.parse_args()
    load_env()

    # Resolve config directory
    opencode_dir = os.environ.get("OPENCODE_DIR")
    if opencode_dir:
        config_dir_path = os.path.abspath(os.path.expanduser(opencode_dir))
    else:
        config_dir_path = os.path.join(os.path.expanduser("~"), ".config", "opencode")

    # Load .env for API keys
    env_path = os.path.join(config_dir_path, ".env")
    if not load_env(env_path):
        # Try home .env
        home_env = os.path.expanduser("~/.env")
        load_env(home_env)

    # Build voice config
    voice_config = get_voice_config(
        args.preset,
        _voice_resolution_options(
            args.local_fallback_preset,
            args.role_models,
            args.category_models,
            args.min_reasoning_embedding,
            args.stt_transport,
        ),
    )
    unresolved = voice_config.pop("_voice_unresolved", False)

    output_path = os.path.join(config_dir_path, "tui.json")
    tui_config = {"plugin": []}
    if os.path.exists(output_path):
        try:
            with open(output_path, "r", encoding="utf-8") as config_file:
                tui_config = json.load(config_file)
            if not isinstance(tui_config, dict):
                raise ValueError("tui.json root must be an object")
        except (OSError, json.JSONDecodeError, ValueError) as error:
            logger.critical(f"Could not read existing tui.json safely: {error}")
            return 1
    existing_plugins = tui_config.get("plugin", [])
    if not isinstance(existing_plugins, list):
        logger.critical(
            "Could not merge voice plugin: tui.json 'plugin' must be a list"
        )
        return 1
    filtered_plugins = [
        entry
        for entry in existing_plugins
        if not (
            isinstance(entry, list) and entry and entry[0] == "@renjfk/opencode-voice"
        )
    ]
    existing_voice = next(
        (
            entry
            for entry in existing_plugins
            if isinstance(entry, list)
            and entry
            and entry[0] == "@renjfk/opencode-voice"
        ),
        None,
    )
    if unresolved:
        if existing_voice is not None:
            filtered_plugins.append(existing_voice)
    else:
        filtered_plugins.append(["@renjfk/opencode-voice", voice_config])
    tui_config["plugin"] = filtered_plugins

    # Dry-run mode
    if args.dry_run:
        print("# Dry-run: proposed voice configuration")
        print(f"# Tier: {args.preset}")
        print(f"# Config dir: {config_dir_path}")
        print()
        if unresolved:
            print("Voice outcome: unresolved; existing selection preserved")
        elif not existing_voice or existing_voice != [
            "@renjfk/opencode-voice",
            voice_config,
        ]:
            print(json.dumps(["@renjfk/opencode-voice", voice_config], indent=2))
        changed_names = sorted(
            key
            for key in voice_config
            if not existing_voice
            or len(existing_voice) < 2
            or not isinstance(existing_voice[1], dict)
            or existing_voice[1].get(key) != voice_config[key]
        )
        print(f"Changed setting names: {', '.join(changed_names) or 'none'}")
        preserved_names = [
            str(entry[0])
            for entry in existing_plugins
            if isinstance(entry, list)
            and entry
            and entry[0] != "@renjfk/opencode-voice"
        ]
        print(f"Preserved plugin names: {', '.join(preserved_names) or 'none'}")
        return

    # Write tui.json
    os.makedirs(config_dir_path, exist_ok=True)
    output_path = os.path.join(config_dir_path, "tui.json")

    # Backup existing config
    if os.path.exists(output_path) and not args.no_backup:
        backup_path = backup_file(output_path, enabled=True)
        if backup_path:
            logger.info(f"Backed up existing tui.json to {backup_path}")

    try:
        write_text_file(
            output_path,
            json.dumps(tui_config, indent=2) + "\n",
            backup=False,
        )
        logger.info(f"tui.json written to {output_path}")
    except Exception as e:
        logger.critical(f"Failed to write tui.json: {e}")
        sys.exit(1)

    # Summary
    whisper_model = os.environ.get("DOTFILES_WHISPER_MODEL", "ggml-large-v3-turbo.bin")
    piper_voice = os.environ.get("DOTFILES_PIPER_VOICE", "en_US-lessac-high")
    has_stt = "sttEndpoint" in voice_config

    summary_lines = [
        "OpenCode voice plugin configured!",
        "",
        f"Config written to: {output_path}",
    ]
    if unresolved:
        summary_lines.append("Voice outcome: unresolved; existing selection preserved")
    else:
        summary_lines.extend(
            [
                f"  • LLM endpoint: {voice_config['endpoint']}",
                f"  • LLM model: {voice_config['model']}",
            ]
        )
    if "apiKeyEnv" in voice_config:
        summary_lines.append(f"  • LLM API key env: {voice_config['apiKeyEnv']}")
    if has_stt:
        summary_lines.append(f"  • STT endpoint: {voice_config['sttEndpoint']}")
        summary_lines.append(f"  • STT model: {voice_config['sttModel']}")
        if "sttApiKeyEnv" in voice_config:
            summary_lines.append(f"  • STT API key env: {voice_config['sttApiKeyEnv']}")
    else:
        summary_lines.extend(
            [
                "",
                "STT: using local whisper-cli (install: brew install whisper-cpp sox)",
                f"  Whisper model dir: ~/.local/share/whisper-cpp/",
                f"  Recommended model: {whisper_model}",
            ]
        )

    summary_lines.extend(
        [
            "",
            "TTS: using local Piper (install: uv tool install piper-tts)",
            f"  Piper voice dir: ~/.local/share/piper-voices/",
            f"  Recommended voice: {piper_voice}",
            "",
            "To switch tiers: configure-opencode-voice.py --preset <tier>",
            "To install models: run_onchange_07 (DOTFILES_RUN_VOICE_SETUP=1)",
            "",
            "Voice plugin configuration complete!",
        ]
    )
    logger.info("\n".join(summary_lines))


if __name__ == "__main__":
    main()
