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
import json

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
from local_engines import (  # noqa: E402 -- scripts/lib bootstrap.
    active_engines,
    audio_models,
    iter_engine_discovery,
    local_endpoint_for,
    resolve_engine,
)
import tier_registry
from model_catalogues import _configured_litellm_port
from selected_service import (
    Operation,
    Qualification,
    ServiceLocator,
    Transport,
    resolve_stt_transport,
    resolve_transport,
    select,
    assert_renders_from,
)

VOICE_PREFERENCES = {
    "default": {Operation.STT.value: [], Operation.CLEANUP_LLM.value: []}
}

# Named exception: tier-curated remote voice defaults are operator product
# policy (the explicit-override rung), exempt from discovery qualification.
# R4 may add route-qualification evidence; until then these remain UNKNOWN.


def _select_voice_candidate(
    operation, candidates, host="default", previously_selected=None
):
    """Select with host-specific fixture preferences and honest evidence."""
    return select(
        operation,
        candidates,
        preference=list(VOICE_PREFERENCES.get(host, {}).get(operation.value, [])),
        previously_selected=(
            (
                previously_selected["provider"],
                previously_selected["model"],
                Qualification(previously_selected["qualification"]),
            )
            if isinstance(previously_selected, dict)
            else (
                (
                    previously_selected.provider,
                    previously_selected.model,
                    previously_selected.qualification,
                )
                if previously_selected is not None
                else None
            )
        ),
    )


def _service_locator(
    operation, candidates, gate, previously_selected=None, **route_options
):
    selection = _select_voice_candidate(
        operation, candidates, previously_selected=previously_selected
    )
    route = resolve_transport(
        gate,
        selection.qualification,
        wire_model=route_options.get("wire_model", selection.model),
        endpoint=route_options.get("endpoint"),
        key_reference=route_options.get("key_reference"),
        credential_present=route_options.get("credential_present", True),
        credential_required=route_options.get("credential_required", True),
        direct_endpoint=route_options.get("endpoint"),
        direct_wire_model=route_options.get("wire_model", selection.model),
        direct_key_reference=route_options.get("key_reference"),
        direct_credential_required=route_options.get("credential_required", True),
        direct_credential_present=route_options.get("credential_present", True),
        operation=operation,
    )
    return ServiceLocator(selection, route)


def _tier_override_locator(operation, provider, model, endpoint, api_key_env):
    selection = select(operation, [], override=(provider, model))
    rendered = {"provider": provider, "model": model, "endpoint": endpoint}
    assert rendered["provider"] == selection.provider
    assert rendered["model"] == selection.model
    return selection, {
        "provider": rendered["provider"],
        "model": rendered["model"],
        "qualification": selection.qualification.value,
        "reason": f"tier override: {selection.reason}",
        "endpoint": endpoint,
        "apiKeyEnv": api_key_env,
    }


def _render_locator(locator):
    if (
        locator.selection.qualification
        in (Qualification.UNAVAILABLE, Qualification.UNKNOWN)
        or locator.route.transport is Transport.UNAVAILABLE
    ):
        logger.warning(
            "Omitting voice operation: %s",
            locator.route.reason or locator.selection.reason,
        )
        return None
    rendered = {
        "provider": locator.selection.provider,
        "model": locator.selection.model,
        "transport": locator.route.transport,
        "endpoint": locator.route.endpoint,
        "wire_model": locator.route.wire_model,
    }
    assert_renders_from(locator, rendered)
    return rendered


def _voice_config_dir():
    configured = os.environ.get("OPENCODE_DIR")
    return (
        os.path.abspath(os.path.expanduser(configured))
        if configured
        else os.path.join(os.path.expanduser("~"), ".config", "opencode")
    )


def _load_voice_state(config_dir):
    path = os.path.join(config_dir, "opencode-voice.json")
    try:
        with open(path, encoding="utf-8") as state_file:
            return json.load(state_file)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_voice_state(config_dir, state):
    os.makedirs(config_dir, exist_ok=True)
    path = os.path.join(config_dir, "opencode-voice.json")
    write_text_file(
        path, json.dumps(state, indent=2, sort_keys=True) + "\n", backup=False
    )


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


def _voice_llm_capable(model_ref):
    """Require verified completion capability for the voice LLM."""
    provider, _, name = model_ref.partition("/")
    engine_provider = provider if provider in ("ollama", "omlx") else "ollama"
    try:
        details = get_model_details(name, engine_provider)
    except (OSError, ValueError, RuntimeError) as error:
        logger.warning(
            "Voice model capability lookup failed for %s: %s", model_ref, error
        )
        return False
    caps = details.get("capabilities") or []
    if "completion" in caps:
        return True
    logger.info(
        "Voice LLM: skipping STT-only audio winner %s (no completion capability)",
        model_ref,
    )
    return False


def _resolve_local_voice_model(
    registry,
    resolution_preset,
    role_models,
    category_models,
    min_reasoning_embedding,
    previous=None,
):
    """Resolve a verified local completion candidate while preserving discovery status."""
    discovery = iter_engine_discovery("ollama")
    if discovery.status == "unknown_error":
        logger.warning(
            "Local voice discovery unavailable; preserving existing voice selection"
        )
        return None, True, False
    if discovery.status == "ok_empty":
        logger.warning(
            "No local voice models discovered; voice configuration is unresolved"
        )
        return None, True, True
    local_models = list(discovery.records)
    if not local_models:
        return None, False, False
    min_emb = min_reasoning_embedding
    if min_emb is None:
        min_emb = int(os.environ.get("DOTFILES_MIN_REASONING_EMBEDDING", "0") or "0")
    categories = resolve_roles_from_list(
        local_models, min_reasoning_embedding=min_emb, moe_codegen_reuse=False
    )
    tier_registry.apply_placeholder_overrides(categories, category_models)
    resolved_roles = tier_registry.materialize_role_models(
        registry, resolution_preset, categories, role_models
    )
    candidate_refs = [
        (
            categories.get("solo")
            if resolution_preset == "local-solo"
            else categories.get("audio")
        ),
        categories.get("lightweight"),
        resolved_roles.get("librarian"),
    ]
    candidates = []
    for model_ref in dict.fromkeys(item for item in candidate_refs if item):
        if _voice_llm_capable(model_ref):
            provider, _, model = model_ref.partition("/")
            candidates.append(
                (provider or "ollama", model_ref, Qualification.DISCOVERED)
            )
    locator = _service_locator(
        Operation.CLEANUP_LLM,
        candidates,
        (
            "gateway"
            if os.environ.get("DOTFILES_USE_LITELLM_PROXY", "0") in ("1", "true")
            else "direct"
        ),
        previous,
        endpoint=(
            f"http://127.0.0.1:{_configured_litellm_port() or 4000}/v1"
            if os.environ.get("DOTFILES_USE_LITELLM_PROXY", "0").lower()
            in ("1", "true")
            else (
                (local_endpoint_for(candidates[0][0], "openai") or (None, None))[0]
                if candidates
                else None
            )
        ),
        credential_required=False,
    )
    rendered = _render_locator(locator)
    if rendered is None:
        return None, True, False
    return rendered["model"], False, False


def add_voice_transport_arguments(parser):
    """Keep the voice CLI's supported transport options explicit."""
    parser.add_argument(
        "--stt-transport",
        choices=("auto", "local", "openai"),
        default=None,
        help="STT transport policy (auto preserves tier-based behaviour)",
    )


def get_voice_config(tier: str, options=None, state=None) -> dict:
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
    stt_transport = options.get("stt_transport")
    state = state if state is not None else {}
    saved_transport = state.get("sttTransport")
    gateway_gate_value = os.environ.get("DOTFILES_USE_LITELLM_PROXY")
    gateway_gate = gateway_gate_value is not None and gateway_gate_value.lower() in (
        "1",
        "true",
    )
    resolved_transport, transport_reason = resolve_stt_transport(
        stt_transport, gateway_gate=gateway_gate, saved=saved_transport
    )
    voice_config = {}
    locators = {}
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
                voice_model, unresolved, no_local_models = _resolve_local_voice_model(
                    registry,
                    resolution_preset,
                    role_models,
                    category_models,
                    min_reasoning_embedding,
                    state.get("selections", {}).get(Operation.CLEANUP_LLM.value),
                )
                if voice_model:
                    voice_config = local_voice_provider(voice_model)
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
        model = strip_provider_prefix(orchestrator)
        selection, provenance = _tier_override_locator(
            Operation.CLEANUP_LLM,
            "opencode",
            model,
            provider["baseUrl"],
            provider["apiKeyEnv"],
        )
        voice_config = {
            "endpoint": provider["baseUrl"],
            "model": model,
            "apiKeyEnv": provider["apiKeyEnv"],
        }
        locators[Operation.CLEANUP_LLM.value] = provenance

    elif is_anthropic_tier:
        # Anthropic — via Meridian proxy or direct
        endpoint = (
            get_meridian_base_url()
            if use_meridian
            else get_provider_base_url("anthropic")
        )
        key_env = "MERIDIAN_API_KEY" if use_meridian else "ANTHROPIC_API_KEY"
        selection, provenance = _tier_override_locator(
            Operation.CLEANUP_LLM, "anthropic", "claude-sonnet-5-5", endpoint, key_env
        )
        voice_config = {
            "endpoint": endpoint,
            "model": "claude-sonnet-5-5",
            "apiKeyEnv": key_env,
        }
        locators[Operation.CLEANUP_LLM.value] = provenance

    elif is_plus_tier or tier == "pro-plus":
        # OpenAI or Ollama Cloud + OpenAI STT
        endpoint = get_provider_base_url("openai")
        _, provenance = _tier_override_locator(
            Operation.CLEANUP_LLM, "openai", "gpt-6-luna", endpoint, "OPENAI_API_KEY"
        )
        voice_config = {
            "endpoint": endpoint,
            "model": "gpt-6-luna",
            "apiKeyEnv": "OPENAI_API_KEY",
        }
        locators[Operation.CLEANUP_LLM.value] = provenance

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
                    provider, _, model = voice_model.partition("/")
                    candidates = [(provider, voice_model, Qualification.DISCOVERED)]
                    locator = _service_locator(
                        Operation.CLEANUP_LLM,
                        candidates,
                        (
                            "gateway"
                            if os.environ.get("DOTFILES_USE_LITELLM_PROXY", "0").lower()
                            in ("1", "true")
                            else "direct"
                        ),
                        state.get("selections", {}).get(Operation.CLEANUP_LLM.value),
                        endpoint=(
                            local_endpoint_for(provider, "openai") or (None, None)
                        )[0],
                        credential_required=False,
                    )
                    rendered = _render_locator(locator)
                    if rendered:
                        voice_config = local_voice_provider(rendered["model"])
                        locators[Operation.CLEANUP_LLM.value] = locator
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
        candidates = []
        metadata = {}
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
            for item in models:
                model_ref = item["name"]
                candidates.append((provider, model_ref, Qualification.DISCOVERED))
                metadata[(provider, model_ref)] = (endpoint, item)
        if candidates:
            previous = state.get("selections", {}).get(Operation.STT.value)
            locator = _service_locator(
                Operation.STT,
                candidates,
                "gateway" if gateway_gate else "direct",
                previous,
                endpoint=(
                    f"http://127.0.0.1:{_configured_litellm_port() or 4000}/v1"
                    if gateway_gate
                    else metadata[(candidates[0][0], candidates[0][1])][0][0]
                ),
                wire_model=strip_provider_prefix(
                    metadata[(candidates[0][0], candidates[0][1])][1]["name"]
                ),
                credential_required=False,
            )
            endpoint, item = metadata[
                (locator.selection.provider, locator.selection.model)
            ]
            rendered = _render_locator(locator)
            if rendered:
                voice_config.update(
                    sttEndpoint=rendered["endpoint"], sttModel=rendered["wire_model"]
                )
                if endpoint[1] and os.environ.get(endpoint[1], "").strip():
                    voice_config["sttApiKeyEnv"] = endpoint[1]
                locators[Operation.STT.value] = locator
            else:
                for key in ("sttEndpoint", "sttModel", "sttApiKeyEnv"):
                    voice_config.pop(key, None)

    if resolved_transport == "openai":
        candidate = [("openai", "whisper-1", Qualification.DISCOVERED)]
        locator = _service_locator(
            Operation.STT,
            candidate,
            (
                "gateway"
                if os.environ.get("DOTFILES_USE_LITELLM_PROXY", "0") in ("1", "true")
                else "direct"
            ),
            state.get("selections", {}).get(Operation.STT.value),
            endpoint=(
                f"http://127.0.0.1:{_configured_litellm_port() or 4000}/v1"
                if gateway_gate
                else get_provider_base_url("openai")
            ),
            key_reference="LITELLM_OPENCODE_KEY" if gateway_gate else "OPENAI_API_KEY",
            credential_reference="LITELLM_OPENCODE_KEY" if gateway_gate else None,
            wire_model="whisper-1",
            credential_present=has_openai_key,
            credential_required=True,
        )
        rendered = _render_locator(locator)
        if rendered:
            voice_config.update(
                sttEndpoint=rendered["endpoint"],
                sttModel=rendered["wire_model"],
                sttApiKeyEnv="OPENAI_API_KEY",
            )
            locators[Operation.STT.value] = locator
        else:
            for key in ("sttEndpoint", "sttModel", "sttApiKeyEnv"):
                voice_config.pop(key, None)
    elif resolved_transport == "local":
        configure_local_stt()

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
    if unresolved and (no_local_models or is_local_tier):
        logger.warning(
            "Voice configuration unresolved: %s",
            (
                "no local pools discovered"
                if no_local_models
                else "no verified candidates"
            ),
        )
        voice_config["_voice_unresolved"] = True
    voice_config["_voice_state"] = {
        "sttTransport": resolved_transport,
        "sttTransportReason": transport_reason,
        "selections": {
            name: (
                {
                    "provider": locator.selection.provider,
                    "model": locator.selection.model,
                    "qualification": locator.selection.qualification.value,
                    "reason": locator.selection.reason,
                    "transport": locator.route.transport.value,
                    "endpoint": locator.route.endpoint,
                    "wire_model": locator.route.wire_model,
                    **(
                        {"key_reference": locator.route.key_reference}
                        if locator.route.key_reference
                        else {}
                    ),
                }
                if isinstance(locator, ServiceLocator)
                else locator
            )
            for name, locator in locators.items()
        },
    }
    if not voice_config["_voice_state"]["selections"]:
        voice_config["_voice_state"] = None
    if unresolved and no_local_models and not locators:
        return {"_voice_unresolved": True, "_voice_state": None}
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
    prior_state = _load_voice_state(config_dir_path)
    voice_config = (
        get_voice_config(
            args.preset,
            _voice_resolution_options(
                args.local_fallback_preset,
                args.role_models,
                args.category_models,
                args.min_reasoning_embedding,
                args.stt_transport,
            ),
            state=prior_state,
        )
        if "state" in get_voice_config.__code__.co_varnames
        else get_voice_config(
            args.preset,
            _voice_resolution_options(
                args.local_fallback_preset,
                args.role_models,
                args.category_models,
                args.min_reasoning_embedding,
                args.stt_transport,
            ),
        )
    )
    voice_state = voice_config.pop("_voice_state", None)
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
        existing_options = (
            existing_voice[1]
            if existing_voice is not None
            and len(existing_voice) > 1
            and isinstance(existing_voice[1], dict)
            else {}
        )
        if existing_voice is not None:
            for key in ("endpoint", "model", "apiKeyEnv", "ttsVoice"):
                if key in existing_options:
                    voice_config[key] = existing_options[key]
            filtered_plugins.append(existing_voice)
    elif voice_state is not None:
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

    if not unresolved and voice_state:
        _save_voice_state(config_dir_path, voice_state)

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
