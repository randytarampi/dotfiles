"""Contracts for engines contributing to the unified local model pool."""

import os
import re

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
        "profile_provider": "omlx",
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


def _list_omlx_models_strict(include_cloud=False):
    from omlx import list_omlx_models

    return list_omlx_models(strict=True)


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
LOCAL_ENGINES["omlx"]["list_models_strict"] = _list_omlx_models_strict
LOCAL_ENGINES["omlx"]["metadata_lookup"] = _omlx_metadata
LOCAL_ENGINES["omlx"]["metadata_capabilities"] = _omlx_capabilities
LOCAL_ENGINES["ollama"]["metadata_lookup"] = None
LOCAL_ENGINES["ollama"]["audio_discovery"] = lambda: _audio_models("ollama")
LOCAL_ENGINES["omlx"]["audio_discovery"] = lambda: _audio_models("omlx")
LOCAL_ENGINES["omlx"]["audio_opt_out_env"] = "DOTFILES_USE_LOCAL_OMLX"


def iter_engine_models(provider, include_cloud=False):
    """Yield model entries from one active registered engine."""
    if not engine_gate_active(provider):
        return []
    engine = resolve_engine(provider)
    if engine is None:
        return []
    health_check = engine.get("health_check")
    if health_check and not health_check()[0]:
        return []
    try:
        return engine["list_models"](include_cloud)
    except Exception as err:
        logger.info("Local engine %s listing failed: %s", provider, err)
        return []


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
        return engine.get("list_models_strict", engine["list_models"])(include_cloud)
    except Exception as err:
        logger.info("Local engine %s listing failed: %s", provider, err)
        return None


def active_engine_pools(include_cloud=False):
    return {
        provider: iter_engine_models(provider, include_cloud)
        for provider in active_engines()
    }


def _model_identity(model_name):
    """Return a normalized ``(family, params, markers)`` identity for a name.

    ``family`` is the leading alpha run plus any immediately following
    version digits, lowercased and separator-folded (``gemma-4`` →
    ``gemma4``, ``qwen2.5`` → ``qwen2.5``); ``params`` is the first size
    token via :func:`tier_resolve.extract_param_count` (``gemma-4-12B-it-
    MLX-8bit`` → 12, ``qwen3.8:27b-mlx`` → 27); ``markers`` is the set of
    remaining distinguishing tokens (``coder``, ``vl``, ``it`` …) after
    removing the family, any family-prefixed token, sizes, and
    engine/quantization tokens.

    Two models are engine-equivalent only when family, params, and markers
    all match — ``qwen3.8:27b-mlx`` ↔ ``Qwen3.8-27B-MLX-4bit`` merge, while
    ``Qwen4-7B`` never removes ``qwen2.5-coder:7b`` or ``qwen3:7b``.
    """
    from tier_resolve import extract_param_count

    lowered = model_name.lower()
    match = re.match(r"([a-z]+)(?:[-.]?(\d+(?:\.\d+)?))?", lowered)
    base_family = match.group(1) if match else ""
    version = match.group(2) if match and match.group(2) else ""
    family = base_family + (version or "")
    params = extract_param_count(model_name)
    engine_quant_tokens = {"mlx", "mxfp8", "4bit", "8bit", "gguf", "cloud"}
    # "it" (instruct) is not distinguishing across engines: Ollama tags
    # drop it (gemma4:12b-mxfp8) while upstream oMLX names keep it
    # (gemma-4-12B-it-…). True fine-tune markers (coder/vl/audio/math)
    # remain.
    engine_quant_tokens.add("it")
    excluded_prefixes = {base_family, family} if base_family else {family}
    markers = frozenset(
        token
        for token in re.split(r"[-_:./]+", lowered)
        if token
        and token not in engine_quant_tokens
        and token not in excluded_prefixes
        and not any(token.startswith(prefix) for prefix in excluded_prefixes)
        and not re.fullmatch(r"\d+(?:\.\d+)?[bmt]?", token)
    )
    return family, params, markers


def merged_local_pool(include_cloud=False):
    """Merge active engine pools with oMLX-first equivalence preference.

    When an oMLX model is equivalent to an Ollama model — same normalized
    (family, params) identity, or the exact same name — the oMLX entry
    replaces the Ollama entry (oMLX serves MLX-quantized weights natively
    on Apple Silicon). Distinct models are all kept. The dropped Ollama
    name is recorded on the kept entry as ``equivalent_ollama_names`` so
    classification (tier_resolve) can union capability metadata that only
    Ollama reports (e.g. ``audio`` via ``ollama show``); engine pool
    entries themselves carry no capabilities at merge time.
    """
    models = iter_engine_models("ollama", include_cloud)
    ollama_identities = {
        _model_identity(model["name"]): model["name"] for model in models
    }
    for provider in active_engines():
        if provider == "ollama":
            continue
        try:
            engine_models = iter_engine_models(provider, include_cloud)
        except Exception as err:
            logger.info("Local engine %s discovery failed: %s", provider, err)
            continue
        for model in engine_models:
            identity = _model_identity(model.get("name", ""))
            if model.get("name") in ollama_identities.values() or (
                identity[1] and identity in ollama_identities
            ):
                ollama_match = next(
                    (
                        item
                        for item in models
                        if item.get("name") in ollama_identities.values()
                        and (
                            item.get("name") == model.get("name")
                            or _model_identity(item.get("name", "")) == identity
                        )
                    ),
                    None,
                )
                if ollama_match is not None:
                    equivalent_name = ollama_match["name"]
                elif model.get("name") in ollama_identities.values():
                    equivalent_name = model["name"]
                else:
                    equivalent_name = ollama_identities.get(identity)
                models[:] = [
                    item
                    for item in models
                    if _model_identity(item.get("name", "")) != identity
                ]
                if equivalent_name:
                    equivalents = model.setdefault("equivalent_ollama_names", [])
                    if equivalent_name not in equivalents:
                        equivalents.append(equivalent_name)
                models.append(model)
                logger.info(
                    "Local model equivalent for %s; keeping %s entry (ollama equivalent: %s)",
                    model["name"],
                    provider,
                    equivalent_name or model["name"],
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
    """Return the registered engine contract, or None for unknown providers.

    Cloud providers (ollama-cloud, openai, meridian, ...) are not local
    engines and legitimately resolve to None — silent by design so callers
    that probe mixed provider namespaces stay log-clean.
    """
    return LOCAL_ENGINES.get(provider)


def local_endpoint_for(provider, protocol):
    """Return ``(base_url, api_key_env)`` for a supported local protocol."""
    if protocol not in {"openai", "anthropic"}:
        return None
    engine = resolve_engine(provider)
    if engine is None or not engine_gate_active(provider):
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
    if fallback_winner and protocol == "openai":
        return fallback_winner
    if fallback_winner and protocol == "anthropic":
        logger.info(
            "Ollama fallback winner cannot serve %s; no local winner available",
            protocol,
        )
    return None


def local_caddy_target(provider):
    """Return the registered local engine's Caddy path and loopback target."""
    engine = resolve_engine(provider)
    if engine is None:
        return None
    return engine["caddy_path"], f"127.0.0.1:{engine['default_port']}"


def merge_omlx_settings(existing, environ=None):
    """Merge the managed oMLX settings schema, including cache controls.

    Managed keys override the on-disk file only when their environment
    variable is set; unset env preserves the file value (admin-UI tuning
    such as port or cache sizes wins), falling back to upstream defaults
    only on first run when the file has no value yet.
    """
    environ = environ or os.environ
    import copy

    def _env(env_name):
        value = environ.get(env_name)
        return value.strip() if isinstance(value, str) and value.strip() else None

    def _bool(value):
        return str(value).strip().lower() not in {"0", "false", "no", "off"}

    def _bool_strict(value):
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _int(value):
        return int(str(value).strip())

    settings = copy.deepcopy(existing) if isinstance(existing, dict) else {}

    def _section(name):
        section = settings.setdefault(name, {})
        if not isinstance(section, dict):
            section = {}
            settings[name] = section
        return section

    def _override(section, key, env_name, cast, default):
        """Env-set → cast and write; unset → keep existing, else default."""
        raw = _env(env_name)
        if raw:
            section[key] = cast(raw)
        elif key not in section:
            section[key] = cast(default)

    server = _section("server")
    _override(server, "host", "OMLX_HOST", str, "127.0.0.1")
    _override(server, "port", "OMLX_PORT", _int, "8000")
    _override(server, "log_level", "OMLX_LOG_LEVEL", str, "info")

    model = _section("model")
    model_dir_env = _env("OMLX_MODEL_DIR")
    if model_dir_env:
        model["model_dirs"] = [os.path.expanduser(model_dir_env)]
    elif "model_dirs" not in model:
        model["model_dirs"] = [os.path.expanduser("~/.omlx/models")]

    memory = _section("memory")
    guard = _env("OMLX_MEMORY_GUARD")
    if guard:
        if guard == "off":
            memory.pop("memory_guard_tier", None)
            memory["prefill_memory_guard"] = False
        else:
            memory["memory_guard_tier"] = guard
            memory["prefill_memory_guard"] = True
    elif "memory_guard_tier" not in memory:
        # Fresh file without admin-UI tuning: upstream default tier.
        memory["memory_guard_tier"] = "balanced"
        memory["prefill_memory_guard"] = True
    # Env unset + existing tier → preserve both values as-is.

    scheduler = _section("scheduler")
    _override(
        scheduler, "max_concurrent_requests", "OMLX_MAX_CONCURRENT_REQUESTS", _int, "8"
    )

    cache = _section("cache")
    _override(cache, "enabled", "OMLX_CACHE_ENABLED", _bool, "true")
    _override(
        cache,
        "ssd_cache_dir",
        "OMLX_SSD_CACHE_DIR",
        os.path.expanduser,
        "~/.omlx/cache",
    )
    _override(cache, "ssd_cache_max_size", "OMLX_SSD_CACHE_MAX_SIZE", str, "auto")
    _override(cache, "hot_cache_max_size", "OMLX_HOT_CACHE_MAX_SIZE", str, "0")
    _override(
        cache,
        "hot_cache_write_through",
        "OMLX_HOT_CACHE_WRITE_THROUGH",
        _bool_strict,
        "false",
    )
    _override(cache, "initial_cache_blocks", "OMLX_INITIAL_CACHE_BLOCKS", _int, "256")

    for section_name, key, env_name in (
        ("auth", "api_key", "OMLX_API_KEY"),
        ("huggingface", "endpoint", "OMLX_HF_ENDPOINT"),
    ):
        value = _env(env_name)
        if value:
            _section(section_name)[key] = value
        # Env unset → preserve the on-disk value (admin-UI-managed).

    # MCP: fleet consumers (OpenCode, Codex, ACP, Junie…) run their own MCP
    # clients, so backend tool merge into API completions must stay off
    # (upstream default expose_tools=true would silently duplicate tools for
    # every caller). The dashboard Chat UI is unaffected — it calls
    # /v1/mcp/* directly and does not depend on the expose toggle. Pinned
    # unconditionally (not preserve-on-unset) because the upstream default
    # is true: a first-run file or an admin flip must converge back to off
    # unless OMLX_MCP_EXPOSE_TOOLS explicitly enables it. config_path stays
    # admin-UI-managed (no managed env var).
    mcp = _section("mcp")
    expose_raw = _env("OMLX_MCP_EXPOSE_TOOLS")
    mcp["expose_tools"] = _bool_strict(expose_raw) if expose_raw else False

    return settings


def sync_turboquant_kv(model_settings_path, environ=None):
    """Apply OLLAMA_KV_CACHE_TYPE to oMLX per-model TurboQuant KV settings.

    oMLX has no global KV-type knob — TurboQuant is per-model
    (model_settings.json, engine-construction field, reload to apply).
    This keeps ONE canonical env name for "KV cache type" across both
    daemons: OLLAMA_KV_CACHE_TYPE.

      q8_0 → TurboQuant 8-bit enabled
      q4_0 → TurboQuant 4-bit enabled
      f16 / unset / anything else → per-model settings untouched
      (admin-UI tuning wins; disabling happens in the admin UI)

    The per-model files keep every unmanaged key. Returns the number of
    models whose TurboQuant entries changed.
    """
    if environ is None:
        environ = os.environ
    kv_type = (environ.get("OLLAMA_KV_CACHE_TYPE") or "").strip().lower()
    bit_map = {"q8_0": 8, "q4_0": 4}
    if kv_type not in bit_map:
        return 0

    import json
    import tempfile
    from pathlib import Path

    path = Path(model_settings_path)
    try:
        document = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        document = {}
    if not isinstance(document, dict):
        document = {}
    models = document.setdefault("models", {})
    if not isinstance(models, dict):
        models = {}
        document["models"] = models

    bits = bit_map[kv_type]
    changed = 0
    for model_id, profile in models.items():
        if not isinstance(profile, dict):
            continue
        wanted = (
            True,
            bits,
            profile.get("turboquant_skip_last", True),
        )
        current = (
            profile.get("turboquant_kv_enabled", False),
            profile.get("turboquant_kv_bits", 4),
            profile.get("turboquant_skip_last", True),
        )
        if current != wanted:
            profile["turboquant_kv_enabled"] = wanted[0]
            profile["turboquant_kv_bits"] = wanted[1]
            profile["turboquant_skip_last"] = wanted[2]
            changed += 1

    if not changed:
        return 0

    rendered = json.dumps(document, indent=2) + "\n"
    fd, temp_name = tempfile.mkstemp(
        dir=path.parent, prefix=".modelsettings.", text=True
    )
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(rendered)
    os.replace(temp_name, path)
    return changed
