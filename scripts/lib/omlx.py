"""HTTP discovery and metadata helpers for the local oMLX server.

``get_omlx_base_url()`` returns the server origin without ``/v1``;
``check_omlx_daemon()`` returns ``(reachable, info)``. Both are defined in
``constants.py`` and used here to keep endpoint handling consistent.
"""

import json
import copy
import os
import urllib.error
import urllib.parse
import urllib.request

import logger
from constants import get_omlx_base_url

_STATUS_CACHE = {}
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024


def _get_json(path, params=None):
    url = f"{get_omlx_base_url().rstrip('/')}/{path.lstrip('/')}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, method="GET")
    api_key = os.environ.get("OMLX_API_KEY", "").strip()
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(request, timeout=3) as response:
        payload = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(payload) > _MAX_RESPONSE_BYTES:
            raise ValueError("oMLX response exceeds 5 MB limit")
        return json.loads(payload.decode("utf-8"))


def _status_index():
    """Fetch and index the all-model status response once per endpoint."""
    base_url = get_omlx_base_url().rstrip("/")
    if base_url in _STATUS_CACHE:
        return _STATUS_CACHE[base_url]
    try:
        payload = _get_json("/v1/models/status")
        entries = payload.get("data") or payload.get("models", [])
        index = {}
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            for key in (entry.get("id"), entry.get("model_alias")):
                if key:
                    index[key] = entry
        _STATUS_CACHE[base_url] = index
        return index
    except Exception as err:
        logger.info(f"oMLX model status unavailable: {err}")
        return {}


def get_omlx_model_status(name):
    """Return richer status metadata for one model, or an empty dict.

    The all-model status response is cached so callers do not issue one HTTP
    request per model during a discovery pass.
    """
    return _status_index().get(name, {})


def map_omlx_capabilities(model_entry):
    """Map oMLX metadata to the repository's capability vocabulary.

    oMLX does not expose Ollama's capabilities array. Tool support is therefore
    optimistic for llm/vlm entries because those model types are served through
    the tool-capable OpenAI/Anthropic endpoints; explicit tool/template fields
    still take precedence when present. Upstream status does not currently
    expose reliable override fields, so this carries a documented false-positive risk.
    Unknown model types receive no capabilities and are excluded downstream
    rather than being assumed to be language models.
    """
    model_type = model_entry.get("model_type", "")
    capabilities = set()
    if model_type in {"llm", "vlm"}:
        capabilities.add("completion")
        capabilities.add("tools")
    if model_entry.get("enable_thinking") or model_entry.get("thinking_default"):
        capabilities.add("thinking")
    if model_type == "vlm":
        capabilities.add("vision")
    if model_type in {"audio_stt", "audio_tts", "audio_sts"}:
        capabilities.add("audio")
    if (
        model_entry.get("supports_tools") is False
        or model_entry.get("tool_support") is False
    ):
        capabilities.discard("tools")
    if (
        model_entry.get("supports_tools") is True
        or model_entry.get("tool_support") is True
    ):
        capabilities.add("tools")
    return capabilities


def list_omlx_models(strict=False):
    """List oMLX models in the local discovery shape.

    Embedding, reranker, and unknown-type models carry a separate
    ``primary_category`` marker; tier resolution excludes them from the LLM/VLM
    role pool.
    """
    try:
        payload = _get_json("/v1/models")
        entries = payload.get("data", []) if isinstance(payload, dict) else []
        # Fetch status once per discovery pass (outside the loop): failures are
        # intentionally not cached, so calling inside the loop would retry the
        # endpoint once per model.
        status_by_id = _status_index()
        models = []
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("id"):
                continue
            model_type = entry.get("model_type")
            status = status_by_id.get(entry["id"], {})
            effective_type = model_type or status.get("model_type")
            if not effective_type:
                effective_type = "llm" if status else "unknown"
            merged = {**entry, **status, "model_type": effective_type}
            models.append(
                {
                    "name": entry["id"],
                    "size_gb": round((merged.get("estimated_size") or 0) / 1e9, 2),
                    "provider": "omlx",
                    "model_type": merged.get("model_type") or model_type,
                    # Sorted list (not set): model dicts cross process
                    # boundaries as JSON (configure-jetbrains-ai.py wrapper)
                    # and sets are not JSON-serializable.
                    "capabilities": sorted(map_omlx_capabilities(merged)),
                    "primary_category": effective_type,
                    "is_moe": merged.get("is_moe", merged.get("is_moe_model")),
                    "object": merged.get("object"),
                    "created": merged.get("created"),
                    "owned_by": merged.get("owned_by"),
                    "max_model_len": merged.get("max_model_len"),
                }
            )
        return models
    except Exception as err:
        logger.info(f"oMLX model discovery unavailable: {err}")
        if strict:
            raise
        return []


def merge_omlx_settings(existing, environ=None):
    """Deep-merge managed oMLX settings while preserving unrelated keys."""
    environ = environ or os.environ
    settings = copy.deepcopy(existing) if isinstance(existing, dict) else {}
    for key in (
        "host",
        "port",
        "model_dir",
        "memory_guard",
        "max_concurrent_requests",
        "ssd_cache_dir",
        "log_level",
    ):
        settings.pop(key, None)
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
    cache["ssd_cache_dir"] = os.path.expanduser(
        environ.get("OMLX_SSD_CACHE_DIR", "~/.omlx/cache")
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
