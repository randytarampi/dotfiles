#!/usr/bin/env python3
"""
Configure OpenCode Helper.
Constructs the opencode.json configuration based on presets, mode, local Ollama, and templates.
Also registers the voice plugin in cli.json (v2 TUI-client home) via configure-opencode-voice.py.
"""

from __future__ import annotations

import sys
import json
import argparse
import os
import shutil
import subprocess  # nosec B404 - managed script/gateway orchestration; no shell, env-staged
import tempfile
import stat
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
LIB_DIR = SCRIPT_DIR if SCRIPT_DIR.endswith("lib") else os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger  # noqa: E402 -- local imports follow the scripts/lib sys.path bootstrap.
import opencode_host  # noqa: E402 -- local import follows the bootstrap.
from constants import (  # noqa: E402 -- local import follows bootstrap.
    get_litellm_proxy_mode,
    get_ollama_local_base_url,
    is_meridian_configured,
)
from litellm_aliases import (  # noqa: E402
    canonical_allowlist_key,
    resolve_alias,
)
from model_catalogues import get_models  # noqa: E402 -- scripts/lib bootstrap.
from opencode_config import (  # noqa: E402 -- local import follows bootstrap.
    get_available_tiers,
    build_tier_args,
    get_preset_providers,
    get_slim_config_path,
)
from env import load_env  # noqa: E402 -- local import follows bootstrap.
from caddy_domains import load_domains  # noqa: E402 -- local import follows bootstrap.
from tier_resolve import (  # noqa: E402 -- local import follows bootstrap.
    list_local_ollama_models,
)
from local_engines import (  # noqa: E402 -- local import follows bootstrap.
    engine_gate_active,
    local_provider_block,
    resolve_engine,
)
from opencode_plugins import (  # noqa: E402  # sys.path bootstrap above is intentional.
    active_plugin_specs,
    plugin_specs,
)
from models_dev import (  # noqa: E402 -- local import follows bootstrap.
    fetch_models_dev,
    get_ollama_context_length,
    get_ollama_modalities,
    build_model_entry,
)
from cli_helpers import (  # noqa: E402 -- local import follows bootstrap.
    add_common_args,
    add_skip_arg,
    add_model_override_args,
    add_min_reasoning_embedding_arg,
    forward_common_args,
    forward_model_override_args,
    forward_min_reasoning_embedding_arg,
    parse_skip,
)
from generation_lock import (  # noqa: E402 -- local import follows bootstrap.
    with_generation_lock,
)
from unavailable_presets import (  # noqa: E402
    DISABLED_PRESETS,
    reject_disabled_preset,
    without_disabled_presets,
)
from provider_endpoints import (  # noqa: E402 -- local import follows bootstrap.
    POOLSIDE_ROUTED_CONTEXT,
    POOLSIDE_ROUTED_MODEL_ID,
    POOLSIDE_ROUTED_OUTPUT,
    conservative_limit,
    PROVIDER_ENDPOINTS,
    provider_models,
    selected_optional_providers,
)
from free_preset_registry import PRESETS as DYNAMIC_FREE_PRESETS  # noqa: E402
from free_preset_runtime import prepare_dynamic_free_registry  # noqa: E402

DEFAULT_CADDY_ZONES_CONFIG = "~/.config/caddy/ddns-zones.json"


def expand_path(path_value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(path_value)))


def build_opencode_server_config() -> dict[str, object] | None:
    """Retained compatibility seam; the v2 web service owns this config now."""
    return None


def build_local_provider(provider: str, models: list[dict]) -> dict[str, object] | None:
    """Build an OpenCode local-engine provider from the shared registry."""
    if not engine_gate_active(provider):
        return None
    engine = resolve_engine(provider)
    chat_types = engine.get("chat_model_types") if engine else None
    if chat_types:
        models = [model for model in models if model.get("model_type") in chat_types]
    if not models:
        return None

    def _model_entry(model: dict) -> dict[str, object]:
        # Enrich modalities from the engine's own metadata so OpenCode's
        # client-side attachment gating accepts images for vision-capable
        # models (same mapping the Ollama path uses: vision → image).
        # OpenCode treats custom provider entries without `modalities` as
        # text-only before any API call, even when the backend accepts them.
        entry: dict[str, object] = {"name": model["name"]}
        lookup = engine.get("metadata_lookup") if engine else None
        caps_fn = engine.get("metadata_capabilities") if engine else None
        if lookup and caps_fn:
            try:
                capabilities = set(caps_fn(lookup(model["name"])) or [])
            except Exception:
                capabilities = set()
            input_modalities = ["text"]
            if "vision" in capabilities:
                input_modalities.append("image")
            if "audio" in capabilities:
                input_modalities.append("audio")
            if len(input_modalities) > 1:
                entry["modalities"] = {
                    "input": input_modalities,
                    "output": ["text"],
                }
        return entry

    return local_provider_block(
        provider,
        {model["name"]: _model_entry(model) for model in models},
    )


def register_local_provider(config: dict, provider: str, block: dict | None) -> None:
    """Register a local-engine provider in any generated config branch."""
    if block:
        config["providers"][provider] = block


def _native_permissions(external_dir_permissions, legacy_permission=None):
    """Build the native v2 ordered permissions array.

    v2 evaluates ordered rules with last-match-wins, so the broad
    external_directory ask-all rule leads and the narrower temp-directory
    allow rules follow to win the last match (restoring the v1 behaviour of
    prompting for external paths except the trusted temp dirs). A legacy
    v1 permission map handed to this helper directly (internal callers and
    tests) is converted last; the emitter itself no longer constructs one.
    """
    actions = {"bash": "shell", "task": "subagent", "write": "edit", "patch": "edit"}
    rules = [{"action": "external_directory", "resource": "*", "effect": "ask"}]
    rules.extend(
        {"action": "external_directory", "resource": resource, "effect": effect}
        for resource, effect in external_dir_permissions.items()
    )
    if isinstance(legacy_permission, dict):
        for action, resources in legacy_permission.items():
            if action == "external_directory":
                continue
            if isinstance(resources, dict):
                rules.extend(
                    {
                        "action": actions.get(action, action),
                        "resource": resource,
                        "effect": effect,
                    }
                    for resource, effect in resources.items()
                )
            else:
                rules.append(
                    {
                        "action": actions.get(action, action),
                        "resource": "*",
                        "effect": resources,
                    }
                )
    return rules


_MCP_OAUTH_FIELDS = {
    "clientId": "client_id",
    "clientSecret": "client_secret",
    "callbackPort": "callback_port",
    "redirectUri": "redirect_uri",
}


def _convert_mcp_server_entry(name, entry):
    """Normalize one flat v1 map entry into its native v2 server object.

    Directional precedence: a legacy ``enabled`` value (when present) is
    converted by inversion and wins over any already-native ``disabled``;
    without legacy ``enabled`` the native ``disabled`` value is preserved
    (a native disabled server never silently becomes enabled); malformed
    non-boolean policy values are refused, never coerced.
    """
    server = dict(entry) if isinstance(entry, dict) else {}
    if isinstance(entry, dict) and "enabled" in entry:
        if not isinstance(entry["enabled"], bool):
            raise ValueError(f"MCP server {name!r} has non-boolean enabled")
        server["disabled"] = not entry["enabled"]
        server.pop("enabled", None)
    elif "disabled" in server and not isinstance(server["disabled"], bool):
        raise ValueError(f"MCP server {name!r} has non-boolean disabled")
    else:
        server.setdefault("disabled", False)
    timeout = server.pop("timeout", None)
    if isinstance(timeout, int):
        server["timeout"] = {"catalog": timeout, "execution": timeout}
    for legacy, native in _MCP_OAUTH_FIELDS.items():
        if legacy in server:
            server[native] = server.pop(legacy)
    return server


def _native_mcp_config(mcp_config):
    """Convert the merged v1 flat MCP map to the native v2 mcp.servers shape."""
    if isinstance(mcp_config.get("servers"), dict):
        return mcp_config  # Already native v2.
    servers = {}
    for name, entry in mcp_config.items():
        servers[name] = _convert_mcp_server_entry(name, entry)
    return {"servers": servers}


def _apply_routed_model_limits(model_identity, model):
    if model_identity == POOLSIDE_ROUTED_MODEL_ID and isinstance(model, dict):
        current = model.get("limit")
        model["limit"] = {
            **(current if isinstance(current, dict) else {}),
            "context": conservative_limit(
                current.get("context") if isinstance(current, dict) else None,
                POOLSIDE_ROUTED_CONTEXT,
            ),
            "output": conservative_limit(
                current.get("output") if isinstance(current, dict) else None,
                POOLSIDE_ROUTED_OUTPUT,
            ),
        }
    return model


def apply_litellm_client_gate(config: dict) -> None:
    """Map canonical model references to gateway-only or direct transports."""
    # The gateway keys Anthropic allowlist models under the meridian owner
    # exactly when it serves them (litellm_config mirrors this condition), so
    # the client inventory must use the same namespace.
    meridian_serves = is_meridian_configured() and bool(
        os.environ.get("MERIDIAN_API_KEY", "").strip()
    )

    def inventory_owner(provider_name):
        if provider_name == "anthropic" and meridian_serves:
            return "meridian"
        if provider_name == "cohere":
            # The gateway serves Cohere as cohere_chat/<id> lanes.
            return "cohere_chat"
        return provider_name

    if not get_litellm_proxy_mode():
        return
    if os.environ.get("DOTFILES_RUN_LITELLM_SETUP", "0") != "1":
        raise RuntimeError(
            "DOTFILES_USE_LITELLM_PROXY=1 requires DOTFILES_RUN_LITELLM_SETUP=1"
        )
    base_url = f"http://127.0.0.1:{os.environ.get('LITELLM_PORT', '4000')}/v1"
    key_path = Path("~/.local/share/litellm/clients/opencode.key").expanduser()
    if not key_path.is_file() or key_path.is_symlink():
        raise RuntimeError(f"OpenCode LiteLLM key file is unavailable: {key_path}")
    providers = config.setdefault("providers", {})
    models = {}
    for provider_name, provider in providers.items():
        for model_id, model in (provider.get("models", {}) or {}).items():
            owner = inventory_owner(provider_name)
            identity = (
                f"google/models/{model_id.removeprefix('models/')}"
                if provider_name == "google"
                else f"{owner}/{model_id}"
            )
            models[identity] = {
                **model,
                "id": identity,
                "name": model.get("name", identity),
            }
            _apply_routed_model_limits(identity, models[identity])
    # Registry-wide inventory coverage: the deployed Slim file also selects
    # dormant-preset models whose provider blocks this tier does not build
    # (Google, Open Router, OpenCode Zen, GitHub Copilot). The gateway serves
    # those owners from repo allowlists/live catalogues, so seed the same
    # allowlist-derived identities — never invent aliases beyond them.
    configs_root = Path(__file__).resolve().parents[1] / "configs/opencode"
    for allowlist_provider in (
        "google",
        "openrouter",
        "opencode",
        "github-copilot",
        "openai",
        "ollama-cloud",
        "anthropic",
        "cohere",
        "poolside",
    ):
        allowlist_path = configs_root / f"{allowlist_provider}-models.json"
        if not allowlist_path.is_file():
            continue
        try:
            allowlist = json.loads(allowlist_path.read_text(encoding="utf-8"))["models"]
        except (OSError, json.JSONDecodeError):
            continue
        for model_id, model in allowlist.items():
            # Google allowlist keys are bare while the gateway aliases strip
            # the native 'models/' prefix; OpenRouter allowlist keys use the
            # upstream spellings the gateway now serves verbatim (one alias
            # per model, ':free' variants included), so compose from the raw
            # key. Other providers have no transport-suffix spellings.
            if allowlist_provider == "google":
                model_id = canonical_allowlist_key(model_id)
                identity = f"google/models/{model_id}"
            else:
                identity = f"{inventory_owner(allowlist_provider)}/{model_id}"
            models.setdefault(
                identity,
                {
                    "id": identity,
                    "name": (
                        model.get("name", identity)
                        if isinstance(model, dict)
                        else identity
                    ),
                },
            )
            _apply_routed_model_limits(identity, models[identity])
    # Catalogue honesty: only advertise models the gateway actually serves.
    # The allowlist seeds cover dormant presets; anything the live gateway
    # does not list would otherwise fail at request time, so filter it here
    # (UNKNOWN discipline when the catalogue itself is unavailable).
    try:
        client_key = key_path.read_text(encoding="utf-8").strip()
        catalogue_ids = get_models(f"{base_url}/models", client_key)
        if catalogue_ids is None:
            # get_models returns None for any fetch/auth failure; never
            # convert that to an empty allowlist (UNKNOWN discipline).
            raise ValueError("gateway catalogue unavailable")
        unserved = sorted(set(models) - catalogue_ids)
        if unserved:
            logger.warning(
                "Dropped %d selection(s) the gateway does not serve: %s",
                len(unserved),
                ", ".join(unserved),
            )
        models = {
            identity: entry
            for identity, entry in models.items()
            if identity in catalogue_ids
        }
    except (ValueError, OSError, TimeoutError) as error:
        logger.warning(
            "UNKNOWN gateway catalogue unavailable (%s) — inventory unfiltered",
            error,
        )
    resolution = resolve_alias(
        "openai/gpt-6-luna",
        True,
        client_key=key_path.resolve(),
        gateway_url=base_url,
    )
    config["providers"] = {
        "litellm": {
            "name": "LiteLLM",
            "npm": "@ai-sdk/openai-compatible",
            "options": {
                "baseURL": resolution["base_url"],
                "apiKey": resolution["api_key"],
            },
            "models": models,
        }
    }

    model_fields = {"model", "small_model", "primaryModel", "smallModel"}

    def rewrite_model(value):
        if not isinstance(value, str) or "/" not in value:
            return value
        owner, model_id = value.split("/", 1)
        if not owner or not model_id:
            raise RuntimeError(f"Malformed OpenCode model selection: {value!r}")
        identity = (
            f"google/models/{model_id.removeprefix('models/')}"
            if owner == "google"
            else f"{inventory_owner(owner)}/{model_id}"
        )
        if identity not in models:
            raise RuntimeError(
                f"OpenCode gateway alias is not configured for canonical model {value}"
            )
        return resolve_alias(
            identity, True, client_key=key_path.resolve(), gateway_url=base_url
        )["model"]

    def rewrite_models(node):
        if isinstance(node, dict):
            return {
                key: (
                    rewrite_model(child)
                    if key in model_fields
                    else rewrite_models(child)
                )
                for key, child in node.items()
            }
        if isinstance(node, list):
            return [rewrite_models(child) for child in node]
        return node

    for field in model_fields:
        if field in config:
            config[field] = rewrite_model(config[field])
    for field in (key for key in config if key != "provider"):
        config[field] = rewrite_models(config[field])


def apply_litellm_slim_gate(
    slim: dict,
    config: dict,
    previous_slim=None,
    *,
    preserve_dynamic=False,
    selected_preset=None,
) -> dict:
    """Route every Slim selection using the gateway inventory built for OpenCode."""
    if not get_litellm_proxy_mode():
        return slim
    gateway = config.get("providers", {}).get("litellm", {}).get("models", {})
    dynamic_selected_refs = {
        value["model"]
        for preset in ("free", "omo-slim-opencode-zen-free")
        for value in slim.get("presets", {}).get(preset, {}).values()
        if isinstance(value, dict)
        and isinstance(value.get("model"), str)
        and value["model"].startswith("litellm/")
    }
    available = {
        model.get("id") for model in gateway.values() if isinstance(model, dict)
    }
    key_path = (
        Path("~/.local/share/litellm/clients/opencode.key").expanduser().resolve()
    )
    base_url = f"http://127.0.0.1:{os.environ.get('LITELLM_PORT', '4000')}/v1"
    # Local Ollama and gated local-engine models are direct endpoints, not
    # LiteLLM aliases; project and global inventories can omit their catalogues.
    direct_exceptions = {"meridian"}
    local_owners = {"ollama", "omlx", "llamacpp"}
    previous_values = set()
    if isinstance(previous_slim, dict):

        def collect_previous(node):
            if isinstance(node, dict):
                for field, child in node.items():
                    if field in {"model", "primaryModel"} and isinstance(child, str):
                        previous_values.add(child)
                    elif field == "fallback" and isinstance(child, dict):
                        for chain in child.values():
                            if isinstance(chain, list):
                                previous_values.update(
                                    item for item in chain if isinstance(item, str)
                                )
                    else:
                        collect_previous(child)
            elif isinstance(node, list):
                for child in node:
                    collect_previous(child)

        collect_previous(previous_slim)

    def rewrite_model(value):
        """Resolve one gateway alias, preserving direct exceptions and local models.

        Raise RuntimeError when a selection requires an alias but has no unique
        match in the gateway inventory.
        """
        if not isinstance(value, str):
            return value
        if value.startswith("_local:"):
            return value
        if value.startswith("litellm/"):
            value = value[len("litellm/") :]
            if not value or value.startswith("litellm/") or "/" not in value:
                raise RuntimeError(f"Malformed wrapped LiteLLM selection: {value!r}")
            wrapped = True
        else:
            wrapped = False
        if "/" not in value:
            raise RuntimeError(f"Malformed OpenCode model selection: {value!r}")
        owner, model = value.split("/", 1)
        if not owner or not model:
            raise RuntimeError(f"Malformed OpenCode model selection: {value!r}")
        if owner in direct_exceptions:
            return value
        # Anthropic selections route through the Meridian gateway aliases
        # (same canonical model IDs, meridian/ owner namespace). When
        # Meridian does not serve but the gateway has a direct Anthropic
        # alias, fall back to it — same canonical identity, real coverage.
        # Cohere selections route through the cohere_chat/ wire namespace.
        owner_for_lookup = {
            "anthropic": "meridian",
            "cohere": "cohere_chat",
        }.get(owner, owner)
        exact_alias = f"{owner_for_lookup}/{model}"
        matches = [item for item in available if item == exact_alias]
        if not matches and owner == "anthropic":
            direct_alias = f"anthropic/{model}"
            matches = [item for item in available if item == direct_alias]
        canonical_model = canonical_allowlist_key(model)
        if not matches:
            matches = [
                item
                for item in available
                if item.split("/", 1)[0] == owner_for_lookup
                and canonical_allowlist_key(item.split("/", 1)[1]) == canonical_model
            ]
        if not matches and owner == "anthropic":
            matches = [
                item
                for item in available
                if item.split("/", 1)[0] == "anthropic"
                and canonical_allowlist_key(item.split("/", 1)[1]) == canonical_model
            ]
        if not matches and owner in local_owners:
            if wrapped:
                raise RuntimeError(
                    f"Wrapped LiteLLM selection has no gateway alias: {value}"
                )
            if value in previous_values:
                raise RuntimeError(
                    f"UNRESOLVED OpenCode Slim selection: missing gateway alias "
                    f"{owner}/{model}. Re-run generation once the alias exists."
                )
            raise RuntimeError(
                f"OpenCode Slim selection has no LiteLLM gateway alias: {value}"
            )
        if not matches and owner == "ollama-cloud":
            raise RuntimeError(
                f"OpenCode Slim selection has no LiteLLM gateway alias: {value}"
            )
        if len(matches) != 1:
            if (
                owner == "github-copilot"
                and os.environ.get("DOTFILES_LITELLM_OAUTH_PROVIDERS") != "1"
            ):
                raise RuntimeError(
                    "OpenCode GitHub Copilot selection withheld: enable DOTFILES_LITELLM_OAUTH_PROVIDERS=1"
                )
            raise RuntimeError(
                f"OpenCode Slim selection has no LiteLLM gateway alias: {value}"
            )
        if wrapped and matches[0] not in available:
            raise RuntimeError(
                f"Wrapped LiteLLM selection has no gateway alias: {value}"
            )
        routed = resolve_alias(
            matches[0], True, client_key=key_path, gateway_url=base_url
        )["model"]
        return routed

    model_fields = {"model", "primaryModel"}

    def visit(node):
        if isinstance(node, dict):
            result = {}
            for key, child in node.items():
                if key in model_fields:
                    result[key] = (
                        child
                        if preserve_dynamic
                        and key == "model"
                        and child in dynamic_selected_refs
                        else rewrite_model(child)
                    )
                elif key == "fallback" and isinstance(child, dict):
                    result[key] = {
                        role: (
                            [rewrite_model(model) for model in chain]
                            if isinstance(chain, list)
                            else chain
                        )
                        for role, chain in child.items()
                    }
                else:
                    result[key] = visit(child)
            return result
        if isinstance(node, list):
            return [visit(child) for child in node]
        return node

    import copy

    result = copy.deepcopy(slim)
    if selected_preset is None:
        selected_preset = slim.get("preset")

    presets = result.get("presets", {})
    if not isinstance(presets, dict):
        raise RuntimeError("Slim presets must be an object")
    has_selector = isinstance(selected_preset, str) and bool(selected_preset)

    def validate_section(section, location):
        try:
            return visit(section)
        except RuntimeError as error:
            raise RuntimeError(f"{location}: {error}") from error

    if not has_selector:
        return visit(result)

    if selected_preset not in presets:
        raise RuntimeError(f"Active Slim preset is unavailable: {selected_preset}")
    tiers = result.get("_tiers", {})
    councils = result.get("council", {})
    if not isinstance(tiers, dict) or not isinstance(councils, dict):
        raise RuntimeError("Slim tier and council selectors must be objects")
    council_presets = councils.get("presets", {})
    if not isinstance(council_presets, dict):
        raise RuntimeError("Slim council presets must be an object")
    names = set(presets) | set(tiers) | set(council_presets)
    unavailable = {}
    routed = {}
    for name in names:
        try:
            routed[name] = [
                (
                    validate_section(section[name], f"{label}.{name}")
                    if name in section
                    else None
                )
                for label, section in (
                    ("presets", presets),
                    ("_tiers", tiers),
                    ("council.presets", council_presets),
                )
            ]
        except RuntimeError as error:
            unavailable[name] = error
    # A retained tier cannot point to a council choice that was omitted.
    changed = True
    while changed:
        changed = False
        for name, tier in tiers.items():
            dependency = (
                tier.get("council", {}).get("default_preset")
                if isinstance(tier, dict)
                else None
            )
            if (
                name not in unavailable
                and dependency
                and (dependency not in council_presets or dependency in unavailable)
            ):
                unavailable[name] = RuntimeError(
                    f"Unavailable council choice: {dependency}"
                )
                changed = True
    for name, error in sorted(unavailable.items()):
        logger.warning("Omitting unavailable Slim choice %s: %s", name, error)
    required = {selected_preset}
    if councils.get("default_preset"):
        required.add(councils["default_preset"])
    for name in required:
        if name not in names or name in unavailable:
            raise RuntimeError(
                f"Active Slim selection unavailable: {name}: {unavailable.get(name, 'missing definition')}"
            ) from unavailable.get(name)
    for name in names:
        for index, section in enumerate((presets, tiers, council_presets)):
            if name in unavailable:
                section.pop(name, None)
            elif name in section:
                section[name] = routed[name][index]
    for key, value in list(councils.items()):
        if key != "presets":
            councils[key] = validate_section(value, f"council.{key}")
    for key, value in list(result.items()):
        if key not in {"presets", "_tiers", "council"}:
            result[key] = validate_section(value, key)
    return result


def route_dynamic_free_registry(registry, audit, preset, config):
    """Route only dynamically selected free references through validated aliases."""
    if not get_litellm_proxy_mode():
        return registry
    selected = audit.get("presets", {}).get(preset, {}).get("selected", {})
    if not isinstance(selected, dict) or not selected:
        raise RuntimeError("Dynamic free preset audit has no selected routes")
    key_path = Path("~/.local/share/litellm/clients/opencode.key").expanduser()
    if not key_path.is_file() or key_path.is_symlink():
        raise RuntimeError(f"OpenCode LiteLLM key file is unavailable: {key_path}")
    if os.environ.get("DOTFILES_RUN_LITELLM_SETUP") != "1":
        raise RuntimeError("LiteLLM proxy mode requires the LiteLLM setup gate")
    base_url = f"http://127.0.0.1:{os.environ.get('LITELLM_PORT', '4000')}/v1"
    aliases = set()
    for serving_ref in selected.values():
        if not isinstance(serving_ref, str) or not serving_ref:
            raise RuntimeError(
                "Dynamic free audit contains an invalid serving reference"
            )
        aliases.add(serving_ref)
    sections = [
        registry["presets"][preset],
        registry.get("_tiers", {}).get(preset, {}),
        registry.get("council", {}).get("presets", {}).get(preset, {}),
    ]

    def collect(node):
        if isinstance(node, dict):
            for field, value in node.items():
                if field in {"model", "primaryModel"} and isinstance(value, str):
                    aliases.add(value)
                elif field == "fallback" and isinstance(value, dict):
                    for chain in value.values():
                        if isinstance(chain, list):
                            aliases.update(chain)
                else:
                    collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    for section in sections:
        collect(section)
    models = {}
    for alias in sorted(aliases):
        models[alias] = {"id": alias, "name": alias}
    config["providers"] = {
        "litellm": {
            "name": "LiteLLM",
            "npm": "@ai-sdk/openai-compatible",
            "options": {
                "baseURL": base_url,
                "apiKey": "{file:" + str(key_path.resolve()) + "}",
            },
            "models": models,
        }
    }

    def rewrite(node):
        if isinstance(node, dict):
            result = {}
            for key, child in node.items():
                if key in {"model", "primaryModel"} and isinstance(child, str):
                    result[key] = f"litellm/{child}" if child in aliases else child
                elif key == "fallback" and isinstance(child, dict):
                    result[key] = {
                        role: (
                            [
                                f"litellm/{item}" if item in aliases else item
                                for item in chain
                            ]
                            if isinstance(chain, list)
                            else chain
                        )
                        for role, chain in child.items()
                    }
                else:
                    result[key] = rewrite(child)
            return result
        if isinstance(node, list):
            return [rewrite(item) for item in node]
        return node

    import copy

    result = copy.deepcopy(registry)
    result.setdefault("presets", {})[preset] = rewrite(sections[0])
    result.setdefault("_tiers", {})[preset] = rewrite(sections[1])
    result.setdefault("council", {}).setdefault("presets", {})[preset] = rewrite(
        sections[2]
    )
    return result


def prepare_config_candidates(
    config,
    slim_data,
    previous_slim=None,
    *,
    preserve_dynamic=False,
    selected_preset=None,
):
    """Validate both generated payloads before the caller installs either."""
    if not preserve_dynamic:
        slim_data = without_disabled_presets(slim_data)
    if preserve_dynamic and get_litellm_proxy_mode():
        # The selected dynamic routes were already validated against gateway evidence.
        slim_candidate = slim_data
    else:
        apply_litellm_client_gate(config)
        gate_kwargs = (
            {"selected_preset": selected_preset}
            if selected_preset
            and isinstance(slim_data.get("presets"), dict)
            and slim_data.get("presets")
            else {}
        )
        slim_candidate = apply_litellm_slim_gate(
            slim_data, config, previous_slim, **gate_kwargs
        )
    return (
        json.dumps(config, indent=2) + "\n",
        json.dumps(slim_candidate, indent=2) + "\n",
    )


def prepare_project_candidate(config):
    apply_litellm_client_gate(config)
    return config


def prepare_tier_candidate(
    config_dir,
    tier_args,
    consumer_config,
    base_slim=None,
    previous_slim=None,
    preserve_dynamic=False,
    selected_preset=None,
):
    """Run the tier writer with a private, owner-only consumer config.

    ``base_slim`` seeds the staging directory so the tier writer sees the
    fresh registry presets (not the stale deployed file), allowing dormant
    presets to propagate.  ``previous_slim`` is retained solely for
    ``apply_litellm_slim_gate`` regression tracking.
    """
    with tempfile.TemporaryDirectory(prefix="opencode-tier-") as staging_dir:
        staged_input = Path(staging_dir) / "opencode.json"
        fd, temporary = tempfile.mkstemp(prefix=".opencode.", dir=staging_dir)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(consumer_config, indent=2))
                handle.write("\n")
            os.replace(temporary, staged_input)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise
        staged_env = {**os.environ, "OPENCODE_DIR": staging_dir}
        staged_path = Path(staging_dir) / "oh-my-opencode-slim.json"
        if base_slim is not None:
            write_private_atomic(staged_path, json.dumps(base_slim))
        subprocess.run(  # nosec B603 B607 - repo-root tier script, constant args, staged env
            tier_args, check=True, env=staged_env
        )
        if not staged_path.is_file():
            raise RuntimeError("tier staging did not produce Slim config")
        staged_slim = json.loads(staged_path.read_text(encoding="utf-8"))
        routed = (
            staged_slim
            if preserve_dynamic and get_litellm_proxy_mode()
            else apply_litellm_slim_gate(
                staged_slim,
                consumer_config,
                previous_slim,
                selected_preset=selected_preset or staged_slim.get("preset"),
            )
        )
        return json.dumps(routed, indent=2) + "\n"


def write_private_atomic(path, content):
    """Atomically replace a credential-bearing config with owner-only bytes.

    Accepts ``str`` (encoded UTF-8) or ``bytes`` payloads; byte payloads are
    restored byte-exact so pair recovery never normalises or loses an
    existing generation.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        os.fchmod(fd, 0o600)
        data = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def install_config_pair(slim_path, config_path, slim_payload, config_payload):
    """Install the generated pair, recovering the prior Slim bytes on failure."""
    slim_path = Path(slim_path)
    config_path = Path(config_path)
    prior_slim = slim_path.read_bytes() if slim_path.exists() else None
    slim_mode = stat.S_IMODE(slim_path.stat().st_mode) if slim_path.exists() else None
    slim_mtime_ns = slim_path.stat().st_mtime_ns if slim_path.exists() else None
    candidate_slim = (
        slim_payload.encode("utf-8")
        if isinstance(slim_payload, str)
        else bytes(slim_payload)
    )
    try:
        write_private_atomic(slim_path, slim_payload)
    except Exception as error:
        raise RuntimeError(
            f"Failed to write oh-my-opencode-slim.json: {error}"
        ) from error
    try:
        write_private_atomic(config_path, config_payload)
    except Exception as error:
        try:
            if not slim_path.exists() or slim_path.read_bytes() != candidate_slim:
                raise RuntimeError("second write failed before Slim replacement")
            if prior_slim is not None:
                write_private_atomic(slim_path, prior_slim)
                os.chmod(slim_path, slim_mode)
                os.utime(slim_path, ns=(slim_mtime_ns, slim_mtime_ns))
            elif slim_path.exists():
                os.remove(slim_path)
        except Exception as recovery_error:
            raise RuntimeError(
                f"Failed to write opencode.json: {error}; failed to recover "
                f"oh-my-opencode-slim.json: {recovery_error}"
            ) from error
        raise RuntimeError(
            f"Failed to write opencode.json: {error}; restored the prior "
            "oh-my-opencode-slim.json generation"
        ) from error


def _build_ollama_cloud_model_maps(cloud_models, models_dev_data, local_ollama, preset):
    """Keep local models local and cloud identities on the canonical cloud route."""
    combined_models = {}
    if local_ollama and preset not in ["plus", "pro"]:
        combined_models.update(local_ollama.get("models", {}))
    direct_cloud_models = {
        model_name: build_model_entry(model_name, models_dev_data, "ollama-cloud")
        for model_name in cloud_models
    }
    return combined_models, direct_cloud_models


def _register_ollama_cloud_routes(
    config, combined_models, direct_cloud_models, local_ollama, preset
):
    """Register direct cloud identities; local daemon stubs are never rewrites."""
    if combined_models:
        config["providers"]["ollama"] = {
            "models": combined_models,
            "name": "Ollama",
            "npm": "@ai-sdk/openai-compatible",
            "options": {"baseURL": get_ollama_local_base_url()},
        }
    elif local_ollama and preset not in ["plus", "pro"]:
        config["providers"]["ollama"] = local_ollama

    direct_key = os.environ.get("OLLAMA_API_KEY", "").strip()
    if direct_cloud_models and direct_key:
        config["providers"]["ollama-cloud"] = {"models": direct_cloud_models}
        return

    config["disabled_providers"].append("ollama-cloud")
    if not direct_cloud_models:
        return
    unavailable_ids = ", ".join(sorted(direct_cloud_models))
    logger.warning(
        "Ollama Cloud models unavailable because OLLAMA_API_KEY is unset: %s",
        unavailable_ids,
    )


@with_generation_lock("configure-opencode")
def main():
    parser = argparse.ArgumentParser(
        description="Configure OpenCode json generator and orchestration."
    )
    add_common_args(parser, no_backup=True)
    add_model_override_args(parser)
    add_min_reasoning_embedding_arg(parser)
    available_tiers = [
        name
        for name in get_available_tiers()
        if name not in DISABLED_PRESETS or name in DYNAMIC_FREE_PRESETS
    ]
    parser.add_argument(
        "--preset",
        default=None,
        choices=available_tiers,
    )
    parser.add_argument("--mode", default="global", choices=["global", "project"])
    add_skip_arg(parser, ["mcps", "acp-agents", "tier", "voice", "dcp"])
    args = parser.parse_args()
    # One-way migration gate: native v2 configuration is only written for
    # a compatible host. A v1 (or unprobeable) host keeps its last honest
    # v1-shaped config; nothing is written this run.
    host_state, host_major = opencode_host.resolve_opencode_host()
    if not opencode_host.native_emission_decision(host_state, host_major, logger):
        return
    explicit_preset = args.preset
    source_root = Path(SCRIPT_DIR).resolve().parent
    logger.info("config source: %s/configs", source_root)
    chezmoi_source = os.environ.get("CHEZMOI_SOURCE_DIR")
    if chezmoi_source and Path(chezmoi_source).resolve() != source_root:
        logger.warning(
            "config source root %s differs from CHEZMOI_SOURCE_DIR %s; refusing to hide nested-worktree provenance",
            source_root,
            Path(chezmoi_source).resolve(),
        )
    skipped = parse_skip(args.skip, ["mcps", "acp-agents", "tier", "voice", "dcp"])
    failures = 0

    configs_dir_path = os.path.abspath(
        os.path.join(os.path.dirname(SCRIPT_DIR), "configs")
    )

    opencode_dir = os.environ.get("OPENCODE_DIR")
    if opencode_dir:
        config_dir_path = os.path.abspath(os.path.expanduser(opencode_dir))
    else:
        config_dir_path = os.path.join(os.path.expanduser("~"), ".config", "opencode")

    # Load .env before validating selections so file-provided values cannot bypass the guard.
    env_path = os.path.join(config_dir_path, ".env")
    env_loaded = load_env(env_path)
    if env_loaded:
        logger.info(f"Sourced {env_path}")
    else:
        fallback_path = os.path.join(os.path.expanduser("~"), ".env")
        if load_env(fallback_path):
            logger.info(f"Sourced {fallback_path} (fallback)")
        else:
            logger.warning(
                f"{env_path} not found — run configure-secrets.py first for MCP server configs"
            )

    configured_preset = os.environ.get("DOTFILES_OPENCODE_TIER")
    requested_preset = explicit_preset or configured_preset
    installed_slim_path = os.path.join(config_dir_path, "oh-my-opencode-slim.json")
    if os.path.lexists(installed_slim_path):
        if os.path.islink(installed_slim_path) or not os.path.isfile(
            installed_slim_path
        ):
            parser.error(
                "Cannot safely inspect installed Slim config: expected a regular file"
            )
        try:
            with open(installed_slim_path, "rb") as f:
                installed_bytes = f.read()
            installed_slim = json.loads(installed_bytes)
        except (OSError, ValueError, UnicodeDecodeError):
            parser.error(
                "Cannot safely inspect installed Slim config; refusing overwrite"
            )
        if not isinstance(installed_slim, dict):
            parser.error(
                "Cannot safely inspect installed Slim config: expected an object"
            )
        disabled = set()
        if (
            isinstance(installed_slim, dict)
            and isinstance(installed_slim.get("preset"), str)
            and installed_slim.get("preset") in DISABLED_PRESETS
        ):
            disabled.add(installed_slim["preset"])
        council = (
            installed_slim.get("council") if isinstance(installed_slim, dict) else None
        )
        if (
            isinstance(council, dict)
            and isinstance(council.get("default_preset"), str)
            and council.get("default_preset") in DISABLED_PRESETS
        ):
            disabled.add(council["default_preset"])

        def scan_fallbacks(node):
            if isinstance(node, dict):
                for key, child in node.items():
                    if key == "fallback" and isinstance(child, dict):
                        for chain in child.values():
                            if isinstance(chain, list):
                                disabled.update(
                                    item
                                    for item in chain
                                    if isinstance(item, str)
                                    and item in DISABLED_PRESETS
                                )
                    else:
                        scan_fallbacks(child)
            elif isinstance(node, list):
                for child in node:
                    scan_fallbacks(child)

        scan_fallbacks(installed_slim)
        tiers = (
            installed_slim.get("_tiers", {}) if isinstance(installed_slim, dict) else {}
        )
        for tier in tiers.values() if isinstance(tiers, dict) else ():
            tier_council = tier.get("council", {}) if isinstance(tier, dict) else {}
            if (
                isinstance(tier_council, dict)
                and isinstance(tier_council.get("default_preset"), str)
                and tier_council.get("default_preset") in DISABLED_PRESETS
            ):
                disabled.add(tier_council["default_preset"])
        if disabled and not requested_preset:
            parser.error(
                "Installed Slim selects disabled preset(s) "
                f"{', '.join(sorted(disabled))}; choose an explicit --preset replacement"
            )
        if disabled:
            logger.warning(
                "Replacing installed disabled preset(s) %s with explicitly selected %s; "
                "the replacement may use paid routes. Review the route and cost implications before proceeding.",
                ", ".join(sorted(disabled)),
                requested_preset,
            )

    args.preset = requested_preset or "pro-plus"
    try:
        if args.preset not in DYNAMIC_FREE_PRESETS:
            reject_disabled_preset(args.preset)
        reject_disabled_preset(os.environ.get("DOTFILES_LOCAL_FALLBACK_PRESET", ""))
    except ValueError as error:
        parser.error(str(error))

    dynamic_registry = None
    dynamic_audit = None
    if args.preset in DYNAMIC_FREE_PRESETS:
        try:
            with open(get_slim_config_path(), encoding="utf-8") as source_file:
                source_registry = json.load(source_file)
            dynamic_registry, dynamic_audit = prepare_dynamic_free_registry(
                source_registry, [args.preset], os.environ
            )
        except Exception as error:
            logger.critical(
                "Unable to resolve dynamic free preset before writes: %s", error
            )
            sys.exit(1)

    if args.dry_run:
        logger.info(
            f"[dry-run] Would ensure config directory exists: {config_dir_path}"
        )
    else:
        os.makedirs(config_dir_path, exist_ok=True)

    use_local_env = os.environ.get("DOTFILES_USE_LOCAL_OLLAMA", "true").lower()
    with_local_ollama = use_local_env in ["true", "1"]

    local_ollama_models = []
    if with_local_ollama:
        try:
            local_ollama_models = list_local_ollama_models()
        except Exception:
            pass

    # Installed :cloud stubs stay daemon consumers; canonical cloud IDs are
    # never rewritten to stub IDs by this client generator.

    # Generate MCP Config (unless --skip-mcp was passed)
    mcp_config = {}
    # When --skip mcps, preserve existing MCP config instead of erasing it
    if args.mode == "global" and "mcps" in skipped:
        _output_path = os.path.join(config_dir_path, "opencode.json")
        if os.path.exists(_output_path):
            try:
                with open(_output_path, "r", encoding="utf-8") as _f:
                    _existing = json.load(_f)
                mcp_config = _existing.get("mcp", {})
                logger.info(
                    f"Preserving existing MCP config ({len(mcp_config)} servers) from {_output_path}"
                )
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(
                    f"Could not read existing MCP config from {_output_path}: {e}"
                )
    if "mcps" not in skipped:
        mcp_args = [
            sys.executable,
            os.path.join(SCRIPT_DIR, "configure-mcp-tool.py"),
            "--mode",
            args.mode,
            "--env-file",
            env_path,
            "--dry-run",
            "--show-secrets",
            "--no-backup",
            "opencode",
        ]
        mcp_args += forward_common_args(args)
        mcp_json_str = "{}"
        try:
            result = subprocess.run(
                mcp_args, capture_output=True, text=True, timeout=15
            )
            # Extract JSON from dry-run output (skip comment header lines starting with #)
            json_lines = [
                line
                for line in result.stdout.strip().splitlines()
                if not line.strip().startswith("#")
            ]
            mcp_json_str = "\n".join(json_lines).strip()
            if not mcp_json_str:
                mcp_json_str = "{}"
        except Exception as e:
            logger.warning(f"Failed to generate MCP config: {e}")

        try:
            mrg_data = json.loads(mcp_json_str) if mcp_json_str else {}
            mcp_config = mrg_data.get("mcp", {})
        except Exception:
            mcp_config = {}

    # Read externalized models configs
    openai_models_path = os.path.join(
        configs_dir_path, "opencode", "openai-models.json"
    )
    ollama_cloud_models_path = os.path.join(
        configs_dir_path, "opencode", "ollama-cloud-models.json"
    )
    anthropic_models_path = os.path.join(
        configs_dir_path, "opencode", "anthropic-models.json"
    )
    opencode_models_path = os.path.join(
        configs_dir_path, "opencode", "opencode-models.json"
    )
    github_copilot_models_path = os.path.join(
        configs_dir_path, "opencode", "github-copilot-models.json"
    )
    google_models_path = os.path.join(
        configs_dir_path, "opencode", "google-models.json"
    )
    openrouter_models_path = os.path.join(
        configs_dir_path, "opencode", "openrouter-models.json"
    )

    openai_models = {}
    ollama_cloud_models = {}
    anthropic_models = {}
    opencode_models = {}
    github_copilot_models = {}
    google_models = {}
    openrouter_models = {}

    if os.path.exists(openai_models_path):
        with open(openai_models_path, "r", encoding="utf-8") as f:
            openai_models = json.load(f).get("models", {})
    if os.path.exists(ollama_cloud_models_path):
        with open(ollama_cloud_models_path, "r", encoding="utf-8") as f:
            ollama_cloud_models = json.load(f).get("models", {})
    if os.path.exists(anthropic_models_path):
        with open(anthropic_models_path, "r", encoding="utf-8") as f:
            anthropic_models = json.load(f).get("models", {})
    if os.path.exists(opencode_models_path):
        with open(opencode_models_path, "r", encoding="utf-8") as f:
            opencode_models = json.load(f).get("models", {})
    if os.path.exists(github_copilot_models_path):
        with open(github_copilot_models_path, "r", encoding="utf-8") as f:
            github_copilot_models = json.load(f).get("models", {})
    if os.path.exists(google_models_path):
        with open(google_models_path, "r", encoding="utf-8") as f:
            google_models = json.load(f).get("models", {})
    if os.path.exists(openrouter_models_path):
        with open(openrouter_models_path, "r", encoding="utf-8") as f:
            openrouter_models = json.load(f).get("models", {})

    models_dev_data = fetch_models_dev()

    # Decode local ollama
    local_ollama = {}
    local_engine_models = {}
    for model in local_ollama_models:
        if isinstance(model, dict) and model.get("provider") != "ollama":
            local_engine_models.setdefault(model.get("provider"), []).append(model)
    local_engine_providers = {
        provider: block
        for provider, models in local_engine_models.items()
        if (block := build_local_provider(provider, models))
    }
    ollama_models = [
        model
        for model in local_ollama_models
        if not isinstance(model, dict)
        or not (
            (engine := resolve_engine(model.get("provider", "ollama")))
            and engine.get("provider_config")
        )
    ]
    if ollama_models:
        model_names = sorted(
            m["name"] if isinstance(m, dict) else str(m) for m in ollama_models
        )
        # Enrich local Ollama models with context_length from `ollama show`
        # (ground truth — models.dev has no bare `ollama` provider). Local
        # models are free, so no cost field.
        models_obj = {}
        for name in model_names:
            ctx = get_ollama_context_length(name)
            models_obj[name] = build_model_entry(
                name,
                models_dev_data,
                "ollama-cloud",
                ollama_context=ctx,
                # Resolve local/cloud modalities from the models.dev catalog
                # or ollama show; the image hook routes attachments to the observer.
                modalities=get_ollama_modalities(name, models_dev_data),
            )
        local_ollama = local_provider_block("ollama", models_obj)

    # include_anthropic governs the anthropic provider block in global mode.
    # Project mode derives providers from the preset instead (see below).
    include_anthropic = args.preset in [
        "pro-plus-anthropic",
        "plus-anthropic",
        "omo-slim-forty-dollars",
        "anthropic",
    ]

    # Allow access to cross-platform temp directories (/tmp, macOS
    # $TMPDIR, Windows %TEMP%) without prompting. These are used by
    # subagent fanout, build tools, and the orchestrator's own
    # configure-project.py (tempfile.mkdtemp).
    _external_dir_permissions = {
        "/tmp/**": "allow",
        "/var/tmp/**": "allow",
        "/var/folders/**": "allow",
        "/private/tmp/**": "allow",
        "/private/var/folders/**": "allow",
        "~/AppData/Local/Temp/**": "allow",
    }

    configured_plugins = active_plugin_specs()
    for plugin in set(plugin_specs()) - set(configured_plugins):
        logger.info(
            "%s='%s' — skipping optional OpenCode plugin: %s",
            "DOTFILES_RUN_OPENCODE_MEMORY_SETUP",
            "0",
            plugin,
        )

    if args.mode == "project":
        # Project configs must be self-sufficient: emit every provider the
        # selected preset references, and reset disabled_providers so an
        # unrelated global tier cannot suppress them. This makes project
        # presets orthogonal to the global tier (e.g. global `pro` disables
        # anthropic, but a project `anthropic` preset still works).
        try:
            needed_providers = get_preset_providers(args.preset, get_slim_config_path())
            if dynamic_registry is not None:
                needed_providers = {
                    ref["model"].split("/", 1)[0]
                    for ref in dynamic_registry["presets"][args.preset].values()
                    if isinstance(ref, dict) and isinstance(ref.get("model"), str)
                }
        except Exception as e:
            logger.warning(
                f"Could not derive required providers for preset "
                f"'{args.preset}' ({e}); project config may be incomplete."
            )
            needed_providers = set()

        config = {
            "$schema": "https://opencode.ai/config.json",
            "mcp": mcp_config,
            "providers": {},
            "plugins": [],
            "agents": {},
            # Always reset; project presets must not inherit a global tier's
            # disabled_providers (e.g. global `pro` disables anthropic).
            "disabled_providers": [],
        }

        if "openai" in needed_providers and openai_models:
            config["providers"]["openai"] = {
                "models": {
                    mid: build_model_entry(mid, models_dev_data, "openai")
                    for mid in openai_models
                }
            }
        if "anthropic" in needed_providers and anthropic_models:
            config["providers"]["anthropic"] = {
                "models": {
                    mid: build_model_entry(mid, models_dev_data, "anthropic")
                    for mid in anthropic_models
                }
            }
        # Project cloud identities use the direct cloud provider; local daemon
        # stubs are never substituted for canonical IDs.
        if "ollama-cloud" in needed_providers and ollama_cloud_models:
            config["providers"]["ollama-cloud"] = {
                "models": {
                    mid: build_model_entry(mid, models_dev_data, "ollama-cloud")
                    for mid in ollama_cloud_models
                }
            }
        if "ollama" in needed_providers and local_ollama:
            config["providers"]["ollama"] = local_ollama
        for provider, block in local_engine_providers.items():
            register_local_provider(config, provider, block)
        if "opencode" in needed_providers and opencode_models:
            config["providers"]["opencode"] = {
                "models": {
                    mid: build_model_entry(mid, models_dev_data, "opencode")
                    for mid in opencode_models
                },
                "options": {
                    "baseURL": "https://opencode.ai/zen/v1",
                    **(
                        {"apiKey": "{env:OPENCODE_API_KEY}"}
                        if os.environ.get("OPENCODE_API_KEY")
                        else {}
                    ),
                },
            }
        if "github-copilot" in needed_providers and github_copilot_models:
            config["providers"]["github-copilot"] = {
                "models": {
                    mid: build_model_entry(mid, models_dev_data, "github-copilot")
                    for mid in github_copilot_models
                },
                "options": {"baseURL": "https://api.githubcopilot.com"},
            }

        if dynamic_registry is not None:
            selected_roles = dynamic_registry["presets"][args.preset]
            dynamic_models = {}
            for role in selected_roles.values():
                ref = role.get("model") if isinstance(role, dict) else None
                if isinstance(ref, str) and "/" in ref:
                    provider, model_id = ref.split("/", 1)
                    dynamic_models.setdefault(provider, set()).add(model_id)
            for provider, model_ids in dynamic_models.items():
                endpoint = PROVIDER_ENDPOINTS.get(provider)
                if endpoint:
                    config["providers"][provider] = {
                        "models": {
                            model_id: build_model_entry(
                                model_id, models_dev_data, provider
                            )
                            for model_id in model_ids
                        },
                        "options": {
                            "baseURL": endpoint["baseUrl"],
                            "apiKey": "{env:%s}" % endpoint["apiKeyEnv"],
                        },
                    }

        # Mirror oh-my-opencode-slim PR #520: disable the full OpenCode
        # built-in agent set replaced by OMOS (build, explore, general, plan).
        # Preserve any existing agent entries instead of wholesale-replacing.
        for _agent_name in ("build", "explore", "general", "plan"):
            _existing = config["agents"].get(_agent_name)
            config["agents"][_agent_name] = {
                **(_existing if isinstance(_existing, dict) else {}),
                "disabled": True,
            }

        if not config["plugins"]:
            del config["plugins"]
        if not config["mcp"]:
            del config["mcp"]
        if not config["providers"]:
            del config["providers"]
        # Note: disabled_providers is intentionally retained even when empty,
        # so it overrides (clears) any inherited global disabled_providers.

    else:
        try:
            global_needed_providers = get_preset_providers(
                args.preset, get_slim_config_path()
            )
        except Exception as e:
            logger.warning(
                f"Could not derive required providers for preset "
                f"'{args.preset}' ({e}); using legacy global provider rules."
            )
            global_needed_providers = set()
        config = {
            "$schema": "https://opencode.ai/config.json",
            "mcp": mcp_config,
            "compaction": {"auto": False},
            "providers": {},
            "plugins": [
                configured_plugins[0],
                # Manifest order drives the array; the plannotator entry is
                # the one spec emitted as a {package, options} object.
                *[
                    (
                        {
                            "package": plugin_spec,
                            "options": {
                                "workflow": "plan-agent",
                                "planningAgents": [
                                    "orchestrator",
                                    "council",
                                    "claude",
                                    "codex",
                                    "gemini",
                                    "junie",
                                    "copilot",
                                    "opencode",
                                ],
                            },
                        }
                        if plugin_spec.startswith("@plannotator/opencode")
                        else plugin_spec
                    )
                    for plugin_spec in configured_plugins[1:]
                ],
            ],
            "agents": {
                "build": {"disabled": True},
                "explore": {"disabled": True},
                "general": {"disabled": True},
                "plan": {"disabled": True},
            },
            "disabled_providers": [
                "google-vertex-anthropic",
                "google-vertex",
                "amazon-bedrock",
            ],
        }

        if args.preset.startswith("local"):
            if local_ollama:
                config["providers"]["ollama"] = local_ollama

            config["disabled_providers"].extend(["openai", "anthropic", "ollama-cloud"])
        elif args.preset in ("omo-slim-openai", "omo-slim-thirty-dollars"):
            if openai_models:
                config["providers"]["openai"] = {
                    "models": {
                        mid: build_model_entry(mid, models_dev_data, "openai")
                        for mid in openai_models
                    }
                }
            if opencode_models:
                config["providers"]["opencode"] = {
                    "models": {
                        mid: build_model_entry(mid, models_dev_data, "opencode")
                        for mid in opencode_models
                    },
                    "options": {
                        "baseURL": "https://opencode.ai/zen/v1",
                        **(
                            {"apiKey": "{env:OPENCODE_API_KEY}"}
                            if os.environ.get("OPENCODE_API_KEY")
                            else {}
                        ),
                    },
                }
            if args.preset == "omo-slim-thirty-dollars" and github_copilot_models:
                config["providers"]["github-copilot"] = {
                    "models": {
                        mid: build_model_entry(mid, models_dev_data, "github-copilot")
                        for mid in github_copilot_models
                    },
                    "options": {"baseURL": "https://api.githubcopilot.com"},
                }
            config["disabled_providers"].extend(["anthropic", "ollama-cloud", "ollama"])
        elif args.preset in DYNAMIC_FREE_PRESETS:
            selected_roles = dynamic_registry["presets"][args.preset]
            dynamic_models = {}
            for role in selected_roles.values():
                ref = role.get("model") if isinstance(role, dict) else None
                if isinstance(ref, str) and "/" in ref:
                    provider, model_id = ref.split("/", 1)
                    dynamic_models.setdefault(provider, set()).add(model_id)
            needed_providers = set(dynamic_models)
            for provider, model_ids in dynamic_models.items():
                endpoint = PROVIDER_ENDPOINTS.get(provider)
                if endpoint:
                    config["providers"][provider] = {
                        "models": {
                            model_id: build_model_entry(
                                model_id, models_dev_data, provider
                            )
                            for model_id in model_ids
                        },
                        "options": {
                            "baseURL": endpoint["baseUrl"],
                            "apiKey": "{env:%s}" % endpoint["apiKeyEnv"],
                        },
                    }
            config["disabled_providers"].extend(
                provider
                for provider in (
                    "openai",
                    "anthropic",
                    "ollama-cloud",
                    "ollama",
                    "github-copilot",
                    "google",
                    "openrouter",
                    "opencode",
                )
                if provider not in needed_providers
            )
        elif args.preset == "omo-slim-opencode-zen-free":
            if "openai" in global_needed_providers and openai_models:
                config["providers"]["openai"] = {
                    "models": {
                        mid: build_model_entry(mid, models_dev_data, "openai")
                        for mid in openai_models
                    }
                }
            if opencode_models:
                config["providers"]["opencode"] = {
                    "models": {
                        mid: build_model_entry(mid, models_dev_data, "opencode")
                        for mid in opencode_models
                    },
                    "options": {
                        "baseURL": "https://opencode.ai/zen/v1",
                        **(
                            {"apiKey": "{env:OPENCODE_API_KEY}"}
                            if os.environ.get("OPENCODE_API_KEY")
                            else {}
                        ),
                    },
                }
            config["disabled_providers"].extend(
                ["openai", "anthropic", "ollama-cloud", "ollama", "github-copilot"]
            )
            config["disabled_providers"] = [
                provider
                for provider in config["disabled_providers"]
                if provider not in global_needed_providers
            ]
        elif args.preset == "free":
            for provider, models in (
                ("opencode", opencode_models),
                ("google", google_models),
                ("openrouter", openrouter_models),
            ):
                endpoint = PROVIDER_ENDPOINTS[provider]
                if not os.environ.get(endpoint["apiKeyEnv"]):
                    logger.warning(
                        "%s key unavailable — skipping %s provider",
                        endpoint["apiKeyEnv"],
                        provider,
                    )
                    continue
                config["providers"][provider] = {
                    "models": {
                        mid: build_model_entry(mid, models_dev_data, provider)
                        for mid in models
                    },
                    "options": {
                        "baseURL": endpoint["baseUrl"],
                        "apiKey": "{env:%s}" % endpoint["apiKeyEnv"],
                    },
                }
            config["disabled_providers"].extend(
                ["openai", "anthropic", "ollama-cloud", "ollama", "github-copilot"]
            )
            config["disabled_providers"] = [
                provider
                for provider in config["disabled_providers"]
                if provider not in global_needed_providers
            ]
        elif args.preset == "anthropic":
            if include_anthropic and anthropic_models:
                enriched_anthropic = {
                    mid: build_model_entry(mid, models_dev_data, "anthropic")
                    for mid in anthropic_models
                }
                config["providers"]["anthropic"] = {"models": enriched_anthropic}
            config["disabled_providers"].extend(["openai", "ollama-cloud", "ollama"])
        elif args.preset in ("plus-anthropic", "omo-slim-forty-dollars"):
            if openai_models:
                enriched_openai = {
                    mid: build_model_entry(mid, models_dev_data, "openai")
                    for mid in openai_models
                }
                config["providers"]["openai"] = {"models": enriched_openai}
            if include_anthropic and anthropic_models:
                enriched_anthropic = {
                    mid: build_model_entry(mid, models_dev_data, "anthropic")
                    for mid in anthropic_models
                }
                config["providers"]["anthropic"] = {"models": enriched_anthropic}
            config["disabled_providers"].extend(["ollama-cloud", "ollama"])
        else:
            if openai_models:
                enriched_openai = {
                    mid: build_model_entry(mid, models_dev_data, "openai")
                    for mid in openai_models
                }
                config["providers"]["openai"] = {"models": enriched_openai}
            if ollama_cloud_models and args.preset != "plus":
                combined_models, direct_cloud_models = _build_ollama_cloud_model_maps(
                    ollama_cloud_models,
                    models_dev_data,
                    local_ollama,
                    args.preset,
                )
                _register_ollama_cloud_routes(
                    config,
                    combined_models,
                    direct_cloud_models,
                    local_ollama,
                    args.preset,
                )
            elif local_ollama and args.preset not in ["plus", "pro"]:
                # No cloud models at all; local-only provider
                config["providers"]["ollama"] = local_ollama
            if include_anthropic and anthropic_models:
                enriched_anthropic = {
                    mid: build_model_entry(mid, models_dev_data, "anthropic")
                    for mid in anthropic_models
                }
                config["providers"]["anthropic"] = {"models": enriched_anthropic}
            if args.preset == "pro":
                config["providers"].pop("openai", None)
                config["providers"].pop("anthropic", None)
                config["disabled_providers"].extend(["openai", "anthropic", "ollama"])
            elif args.preset == "plus":
                config["providers"].pop("anthropic", None)
                config["disabled_providers"].extend(
                    ["anthropic", "ollama-cloud", "ollama"]
                )

        # Gated oMLX registration for every global tier class (one hoisted
        # call after the tier chain so omlx/ fallbacks always resolve).
        for provider, block in local_engine_providers.items():
            register_local_provider(config, provider, block)

        if not config["mcp"]:
            del config["mcp"]

    # Optional direct inference pools are deliberately disabled in gateway
    # mode. Gateway aliases are independently qualified by the LiteLLM
    # catalogue; direct configuration must not silently bypass that choice.
    if not get_litellm_proxy_mode():
        optional_routes = selected_optional_providers(os.environ)
        for provider, base_url in optional_routes.items():
            if args.mode == "project" and provider not in needed_providers:
                continue
            spec = PROVIDER_ENDPOINTS[provider]
            config.setdefault("provider", {})[provider] = {
                "npm": "@ai-sdk/openai-compatible",
                "options": {
                    "baseURL": base_url,
                    "apiKey": "{env:" + spec["apiKeyEnv"] + "}",
                },
                "models": {
                    mid: build_model_entry(mid, models_dev_data, provider)
                    for mid in provider_models(provider)
                },
            }

    if dynamic_registry is not None and get_litellm_proxy_mode():
        dynamic_registry = route_dynamic_free_registry(
            dynamic_registry, dynamic_audit, args.preset, config
        )

    # Native v2 emission: provider entries intentionally stay in the
    # officially supported V1 format (v2 normalizes supported V1 fields
    # silently; per-entry formats must not mix, so provider/model envelopes
    # convert only after machine-level validation). Native keys converted
    # here: ordered permissions (last-match-wins — the external_directory
    # ask-all rule leads, temp-directory allows follow), mcp.servers, and
    # v2 plugin object entries. v2-ignored fields are dropped.
    config["permissions"] = _native_permissions(
        _external_dir_permissions, config.pop("permissions", None)
    )
    if config.get("mcp"):
        config["mcp"] = _native_mcp_config(config["mcp"])
    config.pop("server", None)
    config.pop("logLevel", None)

    # Prepare both managed outputs before either one is installed.
    output_path = os.path.join(config_dir_path, "opencode.json")

    if args.mode == "project":
        try:
            project_candidate = (
                config
                if dynamic_registry is not None and get_litellm_proxy_mode()
                else prepare_project_candidate(config)
            )
            opencode_candidate = json.dumps(project_candidate, indent=2) + "\n"
        except Exception as e:
            logger.critical(f"Failed to prepare OpenCode project config: {e}")
            sys.exit(1)
        if args.dry_run:
            logger.info(f"[dry-run] Would write opencode.json to {output_path}")
        else:
            write_private_atomic(output_path, opencode_candidate)
        vibeguard_src = os.path.join(
            configs_dir_path, "opencode", "vibeguard.config.json"
        )
        if os.path.exists(vibeguard_src) and not args.dry_run:
            shutil.copy(
                vibeguard_src, os.path.join(config_dir_path, "vibeguard.config.json")
            )
        summary_lines = [
            "OpenCode project config written!",
            "",
            f"Config written to: {output_path}",
            "  • Project MCPs, providers, and agent overrides",
            "",
            "Note: oh-my-opencode-slim.json is NOT written in project mode.",
            "      Use configure-opencode-tier.py in the project directory to set project-tier preset.",
            "Configure script complete!",
        ]
        logger.info("\n".join(summary_lines))
        return

    # global mode:
    # 2. Write vibeguard.config.json
    vibeguard_src = os.path.join(configs_dir_path, "opencode", "vibeguard.config.json")
    # Vibeguard installation is deferred until all candidates validate.

    # 3. Write oh-my-opencode-slim.json
    presets_json_path = os.path.join(
        configs_dir_path, "opencode", "oh-my-opencode-slim.json"
    )
    logger.info(
        f"Writing {os.path.join(config_dir_path, 'oh-my-opencode-slim.json')}..."
    )
    if not os.path.exists(presets_json_path):
        logger.critical(f"presets.json not found at {presets_json_path}")
        sys.exit(1)

    try:
        with open(presets_json_path, "r", encoding="utf-8") as f:
            slim_data = json.load(f)
        if dynamic_registry is not None:
            slim_data = dynamic_registry
            slim_data.setdefault("council", {})["default_preset"] = args.preset
        else:
            slim_data = without_disabled_presets(slim_data)
        previous_slim_path = os.path.join(config_dir_path, "oh-my-opencode-slim.json")
        previous_slim = None
        if os.path.isfile(previous_slim_path):
            try:
                with open(previous_slim_path, encoding="utf-8") as f:
                    previous_slim = json.load(f)
            except (OSError, ValueError):
                raise RuntimeError(
                    "Cannot safely inspect installed Slim config; refusing overwrite"
                ) from None
        opencode_candidate, slim_candidate = prepare_config_candidates(
            config,
            slim_data,
            previous_slim,
            preserve_dynamic=dynamic_registry is not None,
            selected_preset=args.preset,
        )
        tier_candidate = None
        if "tier" not in skipped and not args.dry_run:
            tier_args_list = (
                build_tier_args(
                    tier=args.preset, no_local_fallbacks=not with_local_ollama
                )
                + forward_model_override_args(args)
                + forward_min_reasoning_embedding_arg(args)
            )
            tier_args = [
                sys.executable,
                os.path.join(SCRIPT_DIR, "configure-opencode-tier.py"),
            ] + tier_args_list
            tier_candidate = prepare_tier_candidate(
                config_dir_path,
                tier_args,
                config,
                base_slim=slim_data,
                previous_slim=previous_slim,
                selected_preset=args.preset,
                **({"preserve_dynamic": True} if dynamic_registry is not None else {}),
            )
    except Exception as e:
        logger.critical(f"Failed to prepare OpenCode Slim config: {e}")
        sys.exit(1)

    slim_output_path = os.path.join(config_dir_path, "oh-my-opencode-slim.json")
    if args.dry_run:
        logger.info(
            f"[dry-run] Would write routed oh-my-opencode-slim.json to {config_dir_path}"
        )
        logger.info(f"[dry-run] Would write opencode.json to {output_path}")
    else:
        try:
            install_config_pair(
                slim_output_path,
                output_path,
                tier_candidate if tier_candidate is not None else slim_candidate,
                opencode_candidate,
            )
            logger.info("oh-my-opencode-slim.json written (base config)")
            logger.info("opencode.json written")
        except Exception as error:
            logger.critical("%s", error)
            sys.exit(1)

    vibeguard_src = os.path.join(configs_dir_path, "opencode", "vibeguard.config.json")
    if os.path.exists(vibeguard_src) and not args.dry_run:
        shutil.copy(
            vibeguard_src, os.path.join(config_dir_path, "vibeguard.config.json")
        )
    elif os.path.exists(vibeguard_src):
        logger.info(f"[dry-run] Would copy vibeguard.config.json to {config_dir_path}")

    if args.mode == "project":
        return

    # 3b. Generate and merge acpAgents (ACP-capable agent wrappers)
    acp_agents_source_path = os.path.join(
        configs_dir_path, "opencode", "acp-agents.json"
    )
    if "acp-agents" in skipped:
        logger.info("Skipping ACP agent wrappers")
    elif args.dry_run:
        logger.info(
            "[dry-run] Would run configure-acp-agents.py (skipped: child has no --dry-run)"
        )
    else:
        try:
            acp_args = [
                sys.executable,
                os.path.join(SCRIPT_DIR, "configure-acp-agents.py"),
                "--preset",
                args.preset,
            ]
            acp_result = subprocess.run(
                acp_args,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if acp_result.returncode != 0:
                stderr = (acp_result.stderr or acp_result.stdout or "").strip()
                logger.error(
                    f"ACP agent generation failed (exit {acp_result.returncode}); skipping merge"
                    + (f": {stderr}" if stderr else "")
                )
                failures += 1
            elif not os.path.exists(acp_agents_source_path):
                logger.error(
                    f"ACP agent generation completed but {acp_agents_source_path} was not found; skipping merge"
                )
                failures += 1
            else:
                slim_output_path = os.path.join(
                    config_dir_path, "oh-my-opencode-slim.json"
                )
                try:
                    with open(slim_output_path, "r", encoding="utf-8") as f:
                        slim_data = json.load(f)
                    with open(acp_agents_source_path, "r", encoding="utf-8") as f:
                        acp_data = json.load(f)
                    if isinstance(acp_data, dict) and isinstance(
                        acp_data.get("acpAgents"), dict
                    ):
                        slim_data["acpAgents"] = acp_data["acpAgents"]
                        with open(slim_output_path, "w", encoding="utf-8") as f:
                            json.dump(slim_data, f, indent=2)
                            f.write("\n")
                        logger.info("acpAgents merged into oh-my-opencode-slim.json")
                    else:
                        failures += 1
                        logger.error(
                            f"ACP agent config at {acp_agents_source_path} did not contain acpAgents; skipping merge"
                        )
                except Exception as e:
                    logger.warning(
                        f"Failed to merge acpAgents into oh-my-opencode-slim.json: {e}"
                    )
                    failures += 1
        except Exception as e:
            logger.error(f"Failed to configure ACP agents: {e}")
            failures += 1

    # 4. Set active tier
    if "tier" in skipped:
        logger.info("Skipping active tier configuration")
    else:
        logger.info(f"Setting active tier to {args.preset}...")
    try:
        if "tier" in skipped:
            pass
        else:
            logger.info(f"Active tier set to {args.preset}")
    except Exception as e:
        logger.error(f"Failed to set active tier: {e}")
        failures += 1

    # 5. Configure voice plugin (cli.json)
    if "voice" in skipped:
        logger.info("Skipping voice plugin configuration")
    else:
        logger.info("Configuring voice plugin...")
    try:
        if "voice" in skipped:
            pass
        else:
            # Omit --stt-transport so tier changes preserve the saved microphone policy.
            voice_args = [
                sys.executable,
                os.path.join(SCRIPT_DIR, "configure-opencode-voice.py"),
                "--preset",
                args.preset,
            ] + forward_common_args(args)
            voice_args += forward_model_override_args(args)
            voice_args += forward_min_reasoning_embedding_arg(args)
            if args.no_backup:
                voice_args.append("--no-backup")
            subprocess.run(voice_args, check=True)
            logger.info("Voice plugin configured")
    except Exception as e:
        logger.error(f"Failed to configure voice plugin: {e}")
        failures += 1

    # 5b. DCP: remove obsolete tui.json registration (manifest owns v2 loading)
    if "dcp" in skipped:
        logger.info("Skipping DCP TUI plugin configuration")
    else:
        logger.info("Configuring DCP TUI plugin...")
    try:
        if "dcp" in skipped:
            pass
        else:
            dcp_args = [
                sys.executable,
                os.path.join(SCRIPT_DIR, "configure-opencode-dcp.py"),
            ] + forward_common_args(args)
            if args.no_backup:
                dcp_args.append("--no-backup")
            subprocess.run(dcp_args, check=True)
            logger.info("DCP TUI plugin configured")
    except Exception as e:
        logger.error(f"Failed to configure DCP TUI plugin: {e}")
        failures += 1

    summary_lines = [
        "OpenCode configured!",
        "",
        f"Config written to: {config_dir_path}",
        "  • opencode.json (providers, MCP servers, plugins)",
        f"  • oh-my-opencode-slim.json (all presets, active: {args.preset})",
        "  • vibeguard.config.json (sensitive-string redaction)",
        "  • acp-agents.json (ACP-capable agent wrappers — see ~/.config/opencode/oh-my-opencode-slim.json)",
        "  • cli.json (voice TUI-client plugin config)",
        "",
        "To switch tiers:",
        "     configure-opencode-tier.py --preset pro",
        "     configure-opencode-tier.py --preset pro-plus",
        "     configure-opencode-tier.py --preset pro-plus-anthropic",
        "     configure-opencode-tier.py --preset plus",
        "     configure-opencode-tier.py --preset anthropic",
        "     configure-opencode-tier.py --preset omo-slim-openai",
        "     configure-opencode-tier.py --preset omo-slim-thirty-dollars",
        "     configure-opencode-tier.py --preset omo-slim-opencode-zen-free",
        "     configure-opencode-tier.py --preset local",
        "     configure-opencode-tier.py --preset local-solo",
        "",
        "To regenerate without local ollama:",
        "     set DOTFILES_USE_LOCAL_OLLAMA=0 and re-run configure-opencode.py",
        "",
        "To add/update Meridian proxy plugin:",
        "     configure-meridian.py",
        "",
        "To configure voice plugin separately:",
        "     configure-opencode-voice.py --preset <tier>",
        "",
        "Configure script complete!",
    ]
    logger.info("\n".join(summary_lines))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    raise SystemExit(main())
