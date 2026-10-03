"""Resolve curated Ollama Cloud models to exact installed daemon stub IDs."""

from __future__ import annotations

import os

CLOUD_MODEL_PREFIX = "ollama-cloud/"


def installed_cloud_stub(model_id: str, installed_models: list | None) -> str | None:
    """Return the unique installed cloud stub for a model ID, if unambiguous.

    The model ID may contain repository slashes and inner tags. Only the final
    cloud suffix is considered; no model-name segments are stripped globally.
    """
    if not isinstance(model_id, str) or not model_id or installed_models is None:
        return None
    installed = {
        item if isinstance(item, str) else item.get("name")
        for item in installed_models
        if isinstance(item, str) or isinstance(item, dict)
    }
    installed.discard(None)
    if model_id.endswith((":cloud", "-cloud")):
        candidates = {model_id} if model_id in installed else set()
    else:
        candidates = {
            candidate
            for candidate in (f"{model_id}:cloud", f"{model_id}-cloud")
            if candidate in installed
        }
    return next(iter(candidates)) if len(candidates) == 1 else None


def installed_cloud_stubs(model_ids, installed_models: list | None) -> list[str]:
    """Return exact installed stubs for curated IDs, omitting unresolved IDs."""
    return [
        stub
        for model_id in model_ids
        if (stub := installed_cloud_stub(model_id, installed_models)) is not None
    ]


def proxied_cloud_ref(model_ref: str, installed_models: list | None) -> str:
    """Rewrite a direct Ollama Cloud ref only when its exact stub is installed."""
    prefix = CLOUD_MODEL_PREFIX
    if not isinstance(model_ref, str) or not model_ref.startswith(prefix):
        return model_ref
    model_id = model_ref[len(prefix) :]
    stub = installed_cloud_stub(model_id, installed_models)
    return f"ollama/{stub}" if stub else model_ref


def rewrite_cloud_refs(value, installed_models: list | None):
    """Recursively rewrite cloud refs, preserving direct routes when unresolved."""
    if isinstance(value, str):
        return proxied_cloud_ref(value, installed_models)
    if isinstance(value, list):
        return [rewrite_cloud_refs(item, installed_models) for item in value]
    if isinstance(value, dict):
        return {
            key: rewrite_cloud_refs(item, installed_models)
            for key, item in value.items()
        }
    return value


def unresolved_cloud_model_ids(value, installed_models: list | None) -> set[str]:
    """Return direct cloud IDs lacking a unique installed local stub."""
    if isinstance(value, str):
        prefix = CLOUD_MODEL_PREFIX
        if value.startswith(prefix):
            model_id = value[len(prefix) :]
            return (
                {model_id}
                if installed_cloud_stub(model_id, installed_models) is None
                else set()
            )
        return set()
    if isinstance(value, (list, tuple, set)):
        return set().union(
            *(unresolved_cloud_model_ids(item, installed_models) for item in value)
        )
    if isinstance(value, dict):
        return set().union(
            *(
                unresolved_cloud_model_ids(item, installed_models)
                for item in value.values()
            )
        )
    return set()


def litellm_canary_enabled(client: str) -> bool:
    """Whether this client is explicitly routed through the LiteLLM canary."""
    gate = {
        "opencode": "DOTFILES_OPENCODE_USE_LITELLM",
        "pi": "DOTFILES_PI_USE_LITELLM",
    }.get(client)
    return bool(
        gate
        and os.environ.get("DOTFILES_RUN_LITELLM_SETUP", "0") == "1"
        and os.environ.get(gate, "0") == "1"
    )


def direct_cloud_route_allowed(client: str) -> bool:
    """Direct Ollama Cloud is forbidden while a client LiteLLM canary is on."""
    return not litellm_canary_enabled(client)


def fail_closed_cloud_refs(value, unavailable_model_ids: set[str]):
    """Replace unresolved direct cloud references with an explicit no-model ID."""
    prefix = CLOUD_MODEL_PREFIX
    if isinstance(value, str) and value.startswith(prefix):
        if value[len(prefix) :] in unavailable_model_ids:
            return "ollama/no-model-available"
        return value
    if isinstance(value, list):
        return [fail_closed_cloud_refs(item, unavailable_model_ids) for item in value]
    if isinstance(value, dict):
        return {
            key: fail_closed_cloud_refs(item, unavailable_model_ids)
            for key, item in value.items()
        }
    return value


def fail_closed_all_cloud_refs(value):
    """Replace all direct Ollama Cloud refs when direct routing is forbidden."""
    if isinstance(value, str) and value.startswith(CLOUD_MODEL_PREFIX):
        return "ollama/no-model-available"
    if isinstance(value, list):
        return [fail_closed_all_cloud_refs(item) for item in value]
    if isinstance(value, dict):
        return {key: fail_closed_all_cloud_refs(item) for key, item in value.items()}
    return value
