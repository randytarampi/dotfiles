"""Pure LiteLLM proxy configuration generation from dotfiles registries."""

import json
import logging
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from constants import (
    get_meridian_base_url,
    get_ollama_local_base_url,
    is_meridian_configured,
)
from discover_models import list_cloud_ollama_models
from local_engines import active_engines, iter_engine_models, local_endpoint_for
from provider_endpoints import PROVIDER_ENDPOINTS
from litellm_routing import routing_entries_are_safe
from model_catalogues import open_same_origin

logger = logging.getLogger(__name__)

# Cloud providers whose live /v1/models catalogue the generator enumerates
# into the model list. Non-enumerable providers (openai keyless, opencode
# 403) keep their single curated default alias instead.
LIVE_CATALOGUE_PROVIDERS = ("google", "openrouter", "ollama-cloud")

# Per-client virtual keys provisioned in the LiteLLM proxy (alias → env var
# name persisted in service.env). Shared source for configure-litellm.py
# (provisioning) and verify-config.py (doctor allowlist).
APP_KEYS = {
    "opencode": "LITELLM_OPENCODE_KEY",
    "pi": "LITELLM_PI_KEY",
    "openwebui": "LITELLM_OPENWEBUI_KEY",
    "junie": "LITELLM_JUNIE_KEY",
}


class LiveCatalogueError(Exception):
    """A provider's live model catalogue could not be enumerated."""


class RoutingInvariantError(ValueError):
    """The generated routing table would create a local proxy loop."""


def validate_routing_entries(entries, *, port=4000):
    """Reject entries that route LiteLLM back through Mozart or itself."""
    # Single shared validator: generator and doctor must not keep parallel
    # field-selection implementations that could drift apart (Oracle L2).
    if not routing_entries_are_safe(entries, port=port):
        raise RoutingInvariantError("refusing LiteLLM routing loop")


def _model_name(item):
    return item.get("name") if isinstance(item, dict) else str(item)


def _entry(alias, model, *, api_base=None, key_env=None):
    params = {"model": model}
    if api_base:
        params["api_base"] = api_base
    if key_env:
        params["api_key"] = f"os.environ/{key_env}"
    return {"model_name": alias, "litellm_params": params}


def _live_catalogue(provider, key, base_url, timeout=10):
    """Fetch a provider's live /models IDs, failing closed when unreachable."""
    # bandit B310: provider catalogues are https-only; validate before the
    # request is constructed so urlopen can never see a custom scheme.
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        # bandit B310: audit the constructed URL before urlopen sees it.
        raise LiveCatalogueError(f"refusing non-https catalogue url for {provider}")
    url = urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path.rstrip("/") + "/models", "", "")
    )
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    try:
        with open_same_origin(request, timeout=timeout) as response:
            payload = json.load(response)
    except (urllib.error.URLError, ValueError, TypeError, AttributeError) as error:
        raise LiveCatalogueError(
            f"live model catalogue enumeration failed for {provider}: {error}"
        ) from error
    if not isinstance(payload, dict):
        raise LiveCatalogueError(
            f"live model catalogue for {provider} was not an object"
        )
    data = payload.get("data", [])
    if not isinstance(data, list):
        raise LiveCatalogueError(
            f"live model catalogue for {provider} had invalid data"
        )
    return sorted(
        str(item["id"]) for item in data if isinstance(item, dict) and item.get("id")
    )


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
        models = sorted(_safe_models(provider))
        if provider == "ollama":
            local_base = get_ollama_local_base_url().rstrip("/")
            base = local_base[:-3] if local_base.endswith("/v1") else local_base
            cloud_models = [_model_name(item) for item in list_cloud_ollama_models()]
            # The regular engine pool excludes cloud stubs, but tolerate mixed
            # pools and preserve one pair of aliases per installed model.
            ollama_models = sorted(dict.fromkeys(models + cloud_models))
            for model in ollama_models:
                params = _entry(f"ollama/{model}", f"ollama/{model}", api_base=base)
                bare = _entry(model, f"ollama/{model}", api_base=base)
                entries.extend([params, bare])
            continue
        endpoint = local_endpoint_for(provider, "openai")
        if endpoint is None:
            continue
        base_url, key_env = endpoint
        key_env = key_env if key_env and environ.get(key_env, "").strip() else None
        for model in models:
            entries.extend(
                [
                    _entry(
                        f"{provider}/{model}",
                        f"openai/{model}",
                        api_base=base_url,
                        key_env=key_env,
                    ),
                    _entry(
                        model,
                        f"openai/{model}",
                        api_base=base_url,
                        key_env=key_env,
                    ),
                ]
            )

    if is_meridian_configured() and environ.get("MERIDIAN_API_KEY", "").strip():
        entries.append(
            _entry(
                "meridian/claude-sonnet-5-5",
                "anthropic/claude-sonnet-5-5",
                api_base=get_meridian_base_url(),
                key_env="MERIDIAN_API_KEY",
            )
        )

    clouds = {
        "openai": (
            "OPENAI_API_KEY",
            "openai/gpt-6-luna",
            "https://api.openai.com/v1",
        ),
        "anthropic": (
            "ANTHROPIC_API_KEY",
            "anthropic/claude-sonnet-5-5",
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
            "openai/gpt-6-luna",
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
            if provider in LIVE_CATALOGUE_PROVIDERS:
                live_base = PROVIDER_ENDPOINTS.get(provider, {}).get(
                    "baseUrl", base_url
                )
                live_ids = _live_catalogue(provider, environ[key_env], live_base)
                if live_ids is None:
                    raise LiveCatalogueError(
                        f"live model catalogue enumeration failed for {provider}"
                    )
                if live_ids:
                    entries.extend(
                        _entry(
                            f"{provider}/{model_id}",
                            f"openai/{model_id}",
                            api_base=live_base,
                            key_env=key_env,
                        )
                        for model_id in live_ids
                    )
                    continue
            entries.append(
                _entry(f"{provider}/default", model, api_base=base_url, key_env=key_env)
            )
    try:
        routing_port = int(environ.get("LITELLM_PORT", "4000"))
    except (TypeError, ValueError):
        routing_port = 4000
    validate_routing_entries(entries, port=routing_port)
    return entries


def render_config(environ=None, entries=None):
    environ = environ or os.environ
    if entries is None:
        entries = compute_model_list(environ)
    try:
        routing_port = int(environ.get("LITELLM_PORT", "4000"))
    except (TypeError, ValueError):
        routing_port = 4000
    validate_routing_entries(entries, port=routing_port)
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
            "  database_url: os.environ/DATABASE_URL",
            "",
        ]
    )
    return "\n".join(lines)


def write_config(path, environ=None, entries=None):
    path = Path(path)
    rendered = render_config(environ, entries)
    if path.exists() and path.read_text(encoding="utf-8") == rendered:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".litellm.", text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(rendered)
    os.replace(temp_name, path)
    return True
