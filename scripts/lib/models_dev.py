#!/usr/bin/env python3
"""Model metadata helpers for OpenCode configuration.

Fetches model metadata (context window, cost, output limits) from
models.dev (a community-maintained AI model catalog) and supplements
with ground-truth `context_length` from `ollama show` for Ollama models.

All metadata is returned in OpenCode v2 config schema shape:
  - `limit`: {context, output, input?}
  - `cost`:  {input, output, cache_read?, cache_write?, context_over_200k?}

Graceful degradation: on any failure, returns minimal `{"name": ...}` entries
so OpenCode config generation never breaks due to metadata fetch issues.
"""

import json
import os
import subprocess
import time
import urllib.request

import logger

MODELS_DEV_URL = "https://models.dev/api.json"
CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "dotfiles")
CACHE_FILE = os.path.join(CACHE_DIR, "models-dev.json")
CACHE_TTL_SECONDS = 24 * 60 * 60  # 24 hours

# models.dev providers that map to OpenCode provider keys.
# Note: models.dev has `ollama-cloud` (not bare `ollama`) for cloud models.
SUPPORTED_PROVIDERS = ("openai", "anthropic", "ollama-cloud")


def fetch_models_dev(force_refresh=False):
    """Fetch models.dev catalog, caching to ~/.cache/dotfiles/models-dev.json.

    Returns parsed dict (provider -> provider entry) or {} on failure.
    Cache TTL is 24h. Network failures fall back to stale cache if present.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)

    use_cache = False
    if os.path.exists(CACHE_FILE) and not force_refresh:
        age = time.time() - os.path.getmtime(CACHE_FILE)
        if age < CACHE_TTL_SECONDS:
            use_cache = True

    if use_cache:
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            logger.warning(f"models.dev cache read failed ({exc}); refetching")

    try:
        req = urllib.request.Request(
            MODELS_DEV_URL, headers={"User-Agent": "dotfiles/configure-opencode"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as exc:
            logger.warning(f"models.dev cache write failed: {exc}")
        return data
    except Exception as exc:
        logger.warning(
            f"models.dev fetch failed ({exc}); "
            + ("using stale cache" if os.path.exists(CACHE_FILE) else "no metadata")
        )
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}


def _strip_cost(cost_obj):
    """Return a v2-schema-compliant cost dict, or None if invalid."""
    if not isinstance(cost_obj, dict):
        return None
    out = {}
    for key in ("input", "output", "cache_read", "cache_write"):
        val = cost_obj.get(key)
        if isinstance(val, (int, float)):
            out[key] = val
    # Optional nested over-200k pricing
    over = cost_obj.get("context_over_200k")
    if isinstance(over, dict):
        over_out = {}
        for key in ("input", "output", "cache_read", "cache_write"):
            val = over.get(key)
            if isinstance(val, (int, float)):
                over_out[key] = val
        if over_out:
            out["context_over_200k"] = over_out
    # Must have at least input + output to be useful
    if "input" not in out or "output" not in out:
        return None
    return out


def _strip_limit(limit_obj):
    """Return a v2-schema-compliant limit dict, or None if invalid."""
    if not isinstance(limit_obj, dict):
        return None
    out = {}
    for key in ("context", "input", "output"):
        val = limit_obj.get(key)
        if isinstance(val, (int, float)):
            out[key] = int(val)
    # Must have at least context + output
    if "context" not in out or "output" not in out:
        return None
    return out


def get_model_metadata(provider_key, model_id, models_dev_data):
    """Look up a model in models.dev data.

    Args:
      provider_key: e.g. "openai", "anthropic", "ollama-cloud".
      model_id: model ID without provider prefix.
      models_dev_data: parsed models.dev catalog dict.

    Returns:
      Dict with optional `limit` and `cost` keys (v2-schema shape),
      or {} if not found. Never raises.
    """
    if not models_dev_data:
        return {}
    provider_entry = models_dev_data.get(provider_key)
    if not isinstance(provider_entry, dict):
        return {}
    models = provider_entry.get("models")
    if not isinstance(models, dict):
        return {}
    model = models.get(model_id)
    if not isinstance(model, dict):
        return {}
    result = {}
    limit = _strip_limit(model.get("limit"))
    if limit:
        result["limit"] = limit
    cost = _strip_cost(model.get("cost"))
    if cost:
        result["cost"] = cost
    return result


def get_ollama_context_length(model_name):
    """Run `ollama show <model_name>` and parse `context length`.

    Args:
      model_name: e.g. "glm-5.2:cloud" or "qwen3.5:9b-mlx".

    Returns:
      int context length, or None if unavailable/parse failure.
    """
    try:
        result = subprocess.run(
            ["ollama", "show", model_name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        # Output looks like:
        #   ...
        #   context length      1000000
        #   ...
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("context length"):
                # "context length      1000000" or "context length    1000000"
                parts = stripped.split()
                if len(parts) >= 2:
                    try:
                        return int(parts[-1])
                    except ValueError:
                        continue
        return None
    except FileNotFoundError:
        # ollama binary not on PATH
        return None
    except Exception as exc:
        logger.debug(f"ollama show {model_name} failed: {exc}")
        return None


def build_model_entry(name, models_dev_data, provider_key, ollama_context=None):
    """Assemble an OpenCode v2 model entry with metadata.

    Args:
      name: Display name / model ID for the entry.
      models_dev_data: parsed models.dev catalog (or {}).
      provider_key: models.dev provider key for lookup
        ("openai", "anthropic", "ollama-cloud").
      ollama_context: Optional int from `ollama show` to override
        limit.context (ground truth for Ollama models).

    Returns:
      Dict like {"name": name, "limit": {...}, "cost": {...}}
      or just {"name": name} if no metadata available.
    """
    entry = {"name": name}
    meta = get_model_metadata(provider_key, name, models_dev_data)

    limit = meta.get("limit")
    if ollama_context is not None:
        if limit:
            limit = dict(limit)
            limit["context"] = int(ollama_context)
        else:
            # Ollama show gives us context; default output to a safe value
            # if models.dev didn't provide one. Use context as a reasonable
            # upper bound for output when unknown.
            limit = {"context": int(ollama_context), "output": int(ollama_context)}
    if limit:
        entry["limit"] = limit

    cost = meta.get("cost")
    if cost:
        entry["cost"] = cost

    return entry
