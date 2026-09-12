"""Contracts for engines contributing to the unified local model pool."""

import os
import logger
from constants import check_omlx_daemon, get_omlx_base_url, get_ollama_local_base_url

LOCAL_ENGINES = {
    "ollama": {
        "gate_env": None,
        "api": "ollama-openai",
        "base_url": get_ollama_local_base_url,
        # Local Ollama is unauthenticated by design; OLLAMA_API_KEY belongs to
        # Ollama Cloud and must NOT be wired into local provider blocks.
        "api_key_env": None,
        "anthropic_support": False,
        "gemini_support": True,
        "profile_provider": "ollama",
        "provider_config": False,
        "health_check": None,
        "display_name": "Ollama",
        "npm": "@ai-sdk/openai-compatible",
        "resolve_model": True,
        "context_fallback": None,
        "default_port": "11434",
        "chat_model_types": None,
        "gate_required": False,
        "drift_check": False,
        "details_provider_arg": False,
        "caddy_path": "/ollama/*",
        "port_env": "OLLAMA_LOCAL_PORT",
        "caddy_route": None,
    },
    "omlx": {
        "gate_env": "DOTFILES_RUN_OMLX_SETUP",
        "api": "openai+anthropic",
        "base_url": get_omlx_base_url,
        "api_key_env": "OMLX_API_KEY",
        "anthropic_support": True,
        "gemini_support": False,
        "profile_provider": "omlx-local",
        "provider_config": True,
        "health_check": check_omlx_daemon,
        "display_name": "oMLX",
        "npm": "@ai-sdk/openai-compatible",
        "resolve_model": False,
        "context_fallback": 32768,
        "default_port": "8000",
        "chat_model_types": {"llm", "vlm"},
        "gate_required": True,
        "drift_check": True,
        "details_provider_arg": True,
        "caddy_path": "/omlx/*",
        "port_env": "OMLX_PORT",
        "caddy_route": {
            "blocked": "/admin* /v1/mcp/* /v1/config* /v1/delete* /v1/push*",
            "allowed": "/v1/chat/completions /v1/completions /v1/responses /v1/messages /v1/embeddings /v1/rerank /v1/models /v1/models/status /v1/audio/* /health",
        },
    },
}


def engine_gate_active(provider):
    """Return whether an engine's configured opt-in gate is active."""
    engine = LOCAL_ENGINES.get(provider)
    if engine is None:
        return False
    gate_env = engine.get("gate_env")
    return gate_env is None or os.environ.get(gate_env, "0") == "1"


def active_engines():
    """Return active registered engines in deterministic Ollama-first order."""
    return sorted(
        (provider for provider in LOCAL_ENGINES if engine_gate_active(provider)),
        key=lambda provider: (provider != "ollama", provider),
    )


def _list_ollama_models(include_cloud=False):
    from discover_models import _list_local_ollama_models

    return _list_local_ollama_models(include_cloud)


def _list_omlx_models(include_cloud=False):
    from omlx import list_omlx_models

    return list_omlx_models()


def _omlx_metadata(model_name):
    from omlx import get_omlx_model_status

    return get_omlx_model_status(model_name)


def _audio_models(provider):
    return [
        model
        for model in LOCAL_ENGINES[provider]["list_models"]()
        if model.get("model_type") in {"audio_stt", "audio_tts", "audio_sts"}
        or "audio" in model.get("capabilities", [])
    ]


def _omlx_capabilities(metadata):
    from omlx import map_omlx_capabilities

    return map_omlx_capabilities(metadata)


LOCAL_ENGINES["ollama"]["list_models"] = _list_ollama_models
LOCAL_ENGINES["omlx"]["list_models"] = _list_omlx_models
LOCAL_ENGINES["omlx"]["metadata_lookup"] = _omlx_metadata
LOCAL_ENGINES["omlx"]["metadata_capabilities"] = _omlx_capabilities
LOCAL_ENGINES["ollama"]["metadata_lookup"] = None
LOCAL_ENGINES["ollama"]["audio_discovery"] = lambda: _audio_models("ollama")
LOCAL_ENGINES["omlx"]["audio_discovery"] = lambda: _audio_models("omlx")
LOCAL_ENGINES["omlx"]["audio_opt_out_env"] = "DOTFILES_USE_LOCAL_OMLX"


def iter_engine_models(provider, include_cloud=False):
    """Yield model entries from one active registered engine."""
    models = iter_engine_models_strict(provider, include_cloud)
    return models if models is not None else []


def iter_engine_models_strict(provider, include_cloud=False):
    """Return models, or None when an active engine is unavailable/error."""
    if not engine_gate_active(provider):
        return []
    engine = resolve_engine(provider)
    if engine is None:
        return []
    health_check = engine.get("health_check")
    if health_check and not health_check()[0]:
        return None
    try:
        return engine["list_models"](include_cloud)
    except Exception as err:
        logger.info("Local engine %s listing failed: %s", provider, err)
        return None


def active_engine_pools(include_cloud=False):
    return {
        provider: iter_engine_models(provider, include_cloud)
        for provider in active_engines()
    }


def merged_local_pool(include_cloud=False):
    """Merge active engine pools with Ollama-first name collision precedence."""
    models = iter_engine_models("ollama", include_cloud)
    ollama_names = {model["name"] for model in models}
    for provider in active_engines():
        if provider == "ollama":
            continue
        try:
            engine_models = iter_engine_models(provider, include_cloud)
        except Exception as err:
            logger.info("Local engine %s discovery failed: %s", provider, err)
            continue
        for model in engine_models:
            if model.get("name") in ollama_names:
                logger.info(
                    "Local model collision for %s; keeping Ollama entry", model["name"]
                )
            else:
                models.append(model)
    return models


def audio_models(provider):
    engine = resolve_engine(provider)
    if engine is None or not engine_gate_active(provider):
        return []
    discovery = engine.get("audio_discovery")
    return discovery() if discovery else []


def resolve_engine(provider):
    """Return the registered engine contract, or None for unknown providers."""
    engine = LOCAL_ENGINES.get(provider)
    if engine is None:
        logger.warning(
            "Unknown local engine '%s'; refusing to guess its protocol", provider
        )
    return engine


def local_endpoint_for(provider, protocol):
    """Return ``(base_url, api_key_env)`` for a supported local protocol."""
    if protocol not in {"openai", "anthropic"}:
        return None
    engine = resolve_engine(provider)
    if engine is None:
        return None
    support_key = f"{protocol}_support"
    supports = engine.get(support_key, protocol == "openai")
    if protocol == "anthropic":
        supports = engine.get("anthropic_support", False)
    if not supports:
        return None
    base_url = engine["base_url"]().rstrip("/")
    if not base_url.endswith("/v1"):
        base_url += "/v1"
    return base_url, engine.get("api_key_env") or None


def local_provider_block(provider, models):
    """Build the shared OpenCode-style local provider block for an engine."""
    engine = resolve_engine(provider)
    endpoint = local_endpoint_for(provider, "openai") if engine else None
    if engine is None or endpoint is None:
        return None
    health_check = engine.get("health_check")
    if health_check and not health_check()[0]:
        return None
    base_url, api_key_env = endpoint
    block = {
        "models": models,
        "name": engine["display_name"],
        "npm": engine["npm"],
        "options": {"baseURL": base_url},
    }
    if api_key_env and os.environ.get(api_key_env, "").strip():
        block["options"]["apiKey"] = f"{{env:{api_key_env}}}"
    return block


def resolve_local_winner(models, protocol="openai"):
    """Resolve a validated pool winner, retrying deterministically with Ollama."""
    from tier_resolve import resolve_roles_from_list

    resolved = resolve_roles_from_list(models) if models else {}
    winner = resolved.get("solo") or resolved.get("code-gen")
    provider = winner.split("/", 1)[0] if winner and "/" in winner else "ollama"
    if winner and local_endpoint_for(provider, protocol):
        return winner
    if winner:
        logger.info(
            "Local winner %s cannot serve %s; retrying with Ollama-only pool",
            winner,
            protocol,
        )
    ollama_models = [
        model
        for model in models
        if not isinstance(model, dict) or model.get("provider", "ollama") == "ollama"
    ]
    fallback = resolve_roles_from_list(ollama_models) if ollama_models else {}
    fallback_winner = fallback.get("solo") or fallback.get("code-gen")
    if fallback_winner and (protocol == "openai" or protocol == "anthropic"):
        return fallback_winner
    return None


def local_caddy_target(provider):
    """Return the registered local engine's Caddy path and loopback target."""
    engine = resolve_engine(provider)
    if engine is None:
        return None
    return engine["caddy_path"], f"127.0.0.1:{engine['default_port']}"


def merge_omlx_settings(existing, environ=None):
    """Merge the managed oMLX settings schema, including cache controls."""
    environ = environ or os.environ
    import copy

    settings = copy.deepcopy(existing) if isinstance(existing, dict) else {}
    server = settings.setdefault("server", {})
    if not isinstance(server, dict):
        server = {}
        settings["server"] = server
    server.update(
        {
            "host": environ.get("OMLX_HOST", "127.0.0.1"),
            "port": int(environ.get("OMLX_PORT", "8000")),
            "log_level": environ.get("OMLX_LOG_LEVEL", "info"),
        }
    )
    model = settings.setdefault("model", {})
    if not isinstance(model, dict):
        model = {}
        settings["model"] = model
    model["model_dirs"] = [
        os.path.expanduser(environ.get("OMLX_MODEL_DIR", "~/.omlx/models"))
    ]
    memory = settings.setdefault("memory", {})
    if not isinstance(memory, dict):
        memory = {}
        settings["memory"] = memory
    guard = environ.get("OMLX_MEMORY_GUARD", "balanced")
    if guard == "off":
        memory.pop("memory_guard_tier", None)
        memory["prefill_memory_guard"] = False
    else:
        memory["memory_guard_tier"] = guard
        memory["prefill_memory_guard"] = True
    scheduler = settings.setdefault("scheduler", {})
    if not isinstance(scheduler, dict):
        scheduler = {}
        settings["scheduler"] = scheduler
    scheduler["max_concurrent_requests"] = int(
        environ.get("OMLX_MAX_CONCURRENT_REQUESTS", "8")
    )
    cache = settings.setdefault("cache", {})
    if not isinstance(cache, dict):
        cache = {}
        settings["cache"] = cache
    cache.update(
        {
            "enabled": environ.get("OMLX_CACHE_ENABLED", "true").lower()
            not in {"0", "false", "no", "off"},
            "ssd_cache_dir": os.path.expanduser(
                environ.get("OMLX_SSD_CACHE_DIR", "~/.omlx/cache")
            ),
            "ssd_cache_max_size": environ.get("OMLX_SSD_CACHE_MAX_SIZE", "auto"),
            "hot_cache_max_size": environ.get("OMLX_HOT_CACHE_MAX_SIZE", "0"),
            "hot_cache_write_through": environ.get(
                "OMLX_HOT_CACHE_WRITE_THROUGH", "false"
            ).lower()
            in {"1", "true", "yes", "on"},
            "initial_cache_blocks": int(
                environ.get("OMLX_INITIAL_CACHE_BLOCKS", "256")
            ),
        }
    )
    for section_name, key, env_name in (
        ("auth", "api_key", "OMLX_API_KEY"),
        ("huggingface", "endpoint", "OMLX_HF_ENDPOINT"),
    ):
        value = environ.get(env_name, "").strip()
        section = settings.get(section_name)
        if value:
            if not isinstance(section, dict):
                section = {}
                settings[section_name] = section
            section[key] = value
        elif isinstance(section, dict):
            section.pop(key, None)
            if not section:
                settings.pop(section_name, None)
    return settings
