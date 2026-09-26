"""Pure LiteLLM proxy configuration generation from dotfiles registries."""

import json
import os
import tempfile
from pathlib import Path

from constants import (
    get_meridian_base_url,
    get_ollama_local_base_url,
    is_meridian_configured,
)
from local_engines import active_engines, iter_engine_models, local_endpoint_for
from provider_endpoints import PROVIDER_ENDPOINTS


def _model_name(item):
    return item.get("name") if isinstance(item, dict) else str(item)


def _entry(alias, model, *, api_base=None, key_env=None):
    params = {"model": model}
    if api_base:
        params["api_base"] = api_base
    if key_env:
        params["api_key"] = f"os.environ/{key_env}"
    return {"model_name": alias, "litellm_params": params}


def _safe_models(provider):
    try:
        return [
            _model_name(item)
            for item in iter_engine_models(provider)
            if _model_name(item)
        ]
    except Exception:
        return []


def compute_model_list(environ=None):
    """Return LiteLLM model entries without reading secrets or doing writes."""
    environ = environ or os.environ
    entries = []
    for provider in active_engines():
        models = _safe_models(provider)
        if provider == "ollama":
            local_base = get_ollama_local_base_url().rstrip("/")
            base = local_base[:-3] if local_base.endswith("/v1") else local_base
            entries.extend(
                _entry(f"ollama-{model}", f"ollama/{model}", api_base=base)
                for model in models
            )
            continue
        endpoint = local_endpoint_for(provider, "openai")
        if endpoint is None:
            continue
        base_url, key_env = endpoint
        key_env = key_env if key_env and environ.get(key_env, "").strip() else None
        entries.extend(
            _entry(
                f"{provider}-{model}",
                f"openai/{model}",
                api_base=base_url,
                key_env=key_env,
            )
            for model in models
        )

    if is_meridian_configured() and environ.get("MERIDIAN_API_KEY", "").strip():
        entries.append(
            _entry(
                "meridian-claude-sonnet-5",
                "anthropic/claude-sonnet-5",
                api_base=get_meridian_base_url(),
                key_env="MERIDIAN_API_KEY",
            )
        )

    clouds = {
        "openai": (
            "OPENAI_API_KEY",
            "openai/gpt-5.6-luna",
            "https://api.openai.com/v1",
        ),
        "anthropic": (
            "ANTHROPIC_API_KEY",
            "anthropic/claude-sonnet-5",
            "https://api.anthropic.com",
        ),
        "google": (
            "GEMINI_API_KEY",
            "gemini/gemini-2.5-flash",
            None,
        ),
        "openrouter": (
            "OPENROUTER_API_KEY",
            "openrouter/openai/gpt-4o",
            PROVIDER_ENDPOINTS["openrouter"]["baseUrl"],
        ),
        "opencode": (
            PROVIDER_ENDPOINTS["opencode"]["apiKeyEnv"],
            "openai/gpt-5.6-luna",
            PROVIDER_ENDPOINTS["opencode"]["baseUrl"],
        ),
        "ollama-cloud": (
            "OLLAMA_API_KEY",
            "openai/gpt-oss:120b",
            "https://ollama.com/v1",
        ),
    }
    for provider, (key_env, model, base_url) in clouds.items():
        if environ.get(key_env, "").strip():
            entries.append(
                _entry(f"{provider}-default", model, api_base=base_url, key_env=key_env)
            )
    return entries


def render_config(environ=None):
    environ = environ or os.environ
    entries = compute_model_list(environ)
    lines = ["model_list:" if entries else "model_list: []"]
    for entry in entries:
        params = entry["litellm_params"]
        lines.extend(
            [
                f'  - model_name: {json.dumps(entry["model_name"])}',
                "    litellm_params:",
            ]
        )
        for key, value in params.items():
            if key == "api_key":
                lines.append(f"      {key}: {value}")
            else:
                lines.append(f"      {key}: {json.dumps(value)}")
    lines.extend(
        [
            "litellm_settings:",
            "  drop_params: true",
            # Upstream defaults litellm.telemetry=True (anonymous PostHog usage
            # events); the documented config-level opt-out is telemetry: false.
            "  telemetry: false",
            "general_settings:",
            "  master_key: os.environ/LITELLM_MASTER_KEY",
            "",
        ]
    )
    return "\n".join(lines)


def write_config(path, environ=None):
    path = Path(path)
    rendered = render_config(environ)
    if path.exists() and path.read_text(encoding="utf-8") == rendered:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".litellm.", text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(rendered)
    os.replace(temp_name, path)
    return True
