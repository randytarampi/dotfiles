#!/usr/bin/env python3
from __future__ import annotations

"""
OpenCode Voice Plugin Configurator.

Generates ~/.config/opencode/cli.json with tier-aware voice plugin configuration.
Cleans its managed voice entries from tui.json after a successful cli.json write.
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
import stat
import tempfile
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
from ai_models import (  # noqa: E402 -- scripts/lib bootstrap ordering.
    normalize_provider_model,
    strip_provider_prefix,
)
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
import tier_registry  # noqa: E402
from model_catalogues import _configured_litellm_port  # noqa: E402
from selected_service import (  # noqa: E402
    DirectRoute,
    Operation,
    Qualification,
    CanonicalSelection,
    RouteResolution,
    ServiceLocator,
    Transport,
    resolve_stt_transport,
    resolve_transport,
    select,
    assert_renders_from,
)
from litellm_client_auth import (  # noqa: E402 -- scripts/lib bootstrap ordering.
    resolve_client_auth,
)
from opencode_plugins import active_cli_plugin_specs

VOICE_PLUGIN_KEY = "@bojackduy/opencode-voice"
# The previously managed v1-era voice package: its stale tui.json entries are
# also ours to clean after the cli.json replacement works.
LEGACY_VOICE_PLUGIN_KEY = "@renjfk/opencode-voice"


def _spec_matches_key(value, key):
    if not isinstance(value, str):
        return False
    return value == key or value.startswith(key + "@")


def _voice_entry_matches(entry):
    """Match the voice writer's own cli.json entries (current spec only)."""
    value = entry.get("package") if isinstance(entry, dict) else entry
    if isinstance(value, (list, tuple)) and value:
        value = value[0]
    if not isinstance(value, str):
        return False
    if value.startswith("*") or value.startswith("-") and value[1:].endswith(".*"):
        return False
    value = value.removeprefix("-")
    if value == "*" or value.endswith(".*"):
        return False
    return _spec_matches_key(value, VOICE_PLUGIN_KEY)


def _voice_tui_entry_matches(entry):
    """Match voice entries in tui.json: the current spec plus the package this
    writer managed before the v2 cli.json transition."""
    value = entry.get("package") if isinstance(entry, dict) else entry
    if isinstance(value, (list, tuple)) and value:
        value = value[0]
    if not isinstance(value, str):
        return False
    value = value.removeprefix("-")
    return _spec_matches_key(value, VOICE_PLUGIN_KEY) or _spec_matches_key(
        value, LEGACY_VOICE_PLUGIN_KEY
    )


def _atomic_private_write(path, content):
    directory = os.path.dirname(path)
    fd, temporary = tempfile.mkstemp(dir=directory, prefix=".cli.json.")
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


_ENV_CREDENTIAL_INSTRUCTION_LITERAL = (
    "voice plugin reads LITELLM_OPENCODE_KEY from the environment "
    "(opencode-voice llm-client.js:85); export it, e.g. "
    'export LITELLM_OPENCODE_KEY="$(cat '
    '~/.local/share/litellm/clients/opencode.key)"'
)
_SERVED_ALIAS_UNAVAILABLE = "served alias unavailable"
_OMITTING_TEMPLATE = "Omitting voice operation %s model %s: %s"
_PRO_CLOUD_MODEL = "gemma4:31b:cloud"

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
    operation,
    candidates,
    gate,
    previously_selected=None,
    *,
    selection=None,
    **route_options,
):
    selection = selection or _select_voice_candidate(
        operation, candidates, previously_selected=previously_selected
    )
    if route_options.get("config_only"):
        route = RouteResolution(
            Transport.GATEWAY if gate == "gateway" else Transport.DIRECT,
            route_options.get("endpoint"),
            route_options.get("wire_model", selection.model),
            route_options.get("key_reference"),
            "config-only; runtime unverified",
        )
        return ServiceLocator(selection, route)
    route = resolve_transport(
        gate,
        selection.qualification,
        wire_model=route_options.get("wire_model", selection.model),
        endpoint=route_options.get("endpoint"),
        key_reference=route_options.get("key_reference"),
        credential_present=route_options.get("credential_present", True),
        credential_required=route_options.get("credential_required", True),
        direct=DirectRoute(
            endpoint=route_options.get("endpoint"),
            wire_model=route_options.get("wire_model", selection.model),
            key_reference=route_options.get("key_reference"),
            credential_required=route_options.get("credential_required", True),
            credential_present=route_options.get("credential_present", True),
        ),
        operation=operation,
    )
    return ServiceLocator(selection, route)


def _tier_config_only_locator(operation, provider, model, endpoint, key_env, gate):
    """Construct the operator-curated tier route without claiming runtime evidence."""
    gateway = gate is True
    model = normalize_provider_model(provider, model)
    auth = resolve_client_auth("LITELLM_OPENCODE_KEY") if gateway else None
    alias = _gateway_alias(provider, model, auth.value) if gateway else model
    key_ref = "LITELLM_OPENCODE_KEY" if gateway else key_env
    reason = None
    if gateway and not os.environ.get(key_ref, "").strip():
        reason = _ENV_CREDENTIAL_INSTRUCTION_LITERAL
    elif gateway and (not alias or not auth.authorized):
        reason = (
            auth.detail
            or getattr(_gateway_alias, "last_error", None)
            or _SERVED_ALIAS_UNAVAILABLE
        )
    if gateway and reason:
        logger.warning(
            _OMITTING_TEMPLATE,
            operation.value,
            model,
            reason,
        )
        return None
    selection = CanonicalSelection(
        operation,
        provider,
        model,
        Qualification.UNKNOWN,
        "operator-curated tier default; config-only, runtime unverified",
        config_only=True,
    )
    return _service_locator(
        operation,
        [(provider, model, Qualification.UNKNOWN)],
        "gateway" if gateway else "direct",
        selection=selection,
        endpoint=(
            f"http://127.0.0.1:{_configured_litellm_port() or 4000}/v1"
            if gateway
            else endpoint
        ),
        wire_model=alias,
        key_reference=key_ref,
        config_only=True,
    )


def _render_locator(locator):
    if locator is None:
        logger.warning("Omitting voice operation: no route could be resolved")
        return None
    if (
        not locator.selection.config_only
        and locator.selection.qualification
        in (Qualification.UNAVAILABLE, Qualification.UNKNOWN)
        or locator.route.transport is Transport.UNAVAILABLE
    ):
        logger.warning(
            _OMITTING_TEMPLATE,
            locator.selection.operation.value,
            locator.selection.model,
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
    if not locator.selection.config_only:
        assert_renders_from(locator, rendered)
    return rendered


def _configure_tier_remote(
    voice_config, locators, provider, model, endpoint, api_key_env, gateway_gate
):
    model = normalize_provider_model(provider, model)
    candidates = [(provider, model, Qualification.UNKNOWN)]
    gateway = gateway_gate is True
    served_model = model
    auth = resolve_client_auth("LITELLM_OPENCODE_KEY") if gateway else None
    if gateway:
        served_model = _gateway_alias(provider, model, auth.value)
        if (
            not auth.authorized
            or not os.environ.get("LITELLM_OPENCODE_KEY", "").strip()
        ):
            detail = _ENV_CREDENTIAL_INSTRUCTION_LITERAL
        elif not served_model:
            detail = (
                auth.detail
                or getattr(_gateway_alias, "last_error", None)
                or _SERVED_ALIAS_UNAVAILABLE
            )
        else:
            detail = None
        if detail:
            logger.warning(
                _OMITTING_TEMPLATE,
                Operation.CLEANUP_LLM.value,
                model,
                detail,
            )
            locators[Operation.CLEANUP_LLM.value] = {
                "provider": provider,
                "model": model,
                "qualification": "unavailable",
                "reason": detail,
                "transport": "unavailable",
                "endpoint": None,
                "wire_model": None,
                "key_reference": "LITELLM_OPENCODE_KEY",
            }
            return
    selection = CanonicalSelection(
        Operation.CLEANUP_LLM,
        provider,
        model,
        Qualification.UNKNOWN,
        "operator-curated tier default; config-only, runtime unverified",
        config_only=True,
    )
    route_endpoint = (
        f"http://127.0.0.1:{_configured_litellm_port() or 4000}/v1"
        if gateway
        else endpoint
    )
    locator = _service_locator(
        Operation.CLEANUP_LLM,
        candidates,
        "gateway" if gateway_gate is True else "direct",
        selection=selection,
        endpoint=route_endpoint,
        wire_model=served_model,
        key_reference="LITELLM_OPENCODE_KEY" if gateway else api_key_env,
        credential_present=(
            resolve_client_auth("LITELLM_OPENCODE_KEY").authorized
            if gateway
            else bool(os.environ.get(api_key_env, "").strip())
        ),
        credential_required=True,
        config_only=True,
    )
    rendered = _render_locator(locator)
    provenance = {
        "provider": selection.provider,
        "model": selection.model,
        "qualification": (
            Qualification.UNAVAILABLE.value
            if locator.route.transport is Transport.UNAVAILABLE
            else selection.qualification.value
        ),
        "selection_qualification": selection.qualification.value,
        "candidate_qualification": candidates[0][2].value,
        "reason": locator.route.reason or selection.reason,
        "transport": locator.route.transport.value,
        "endpoint": locator.route.endpoint,
        "wire_model": locator.route.wire_model,
        "key_reference": locator.route.key_reference,
        "config_only": True,
    }
    locators[Operation.CLEANUP_LLM.value] = provenance
    if rendered and (
        locator.route.transport is not Transport.GATEWAY
        or not locator.route.key_reference
        or os.environ.get(locator.route.key_reference, "").strip()
    ):
        # Gateway routes are attached only when their referenced runtime
        # credential resolves to a non-empty value (the plugin reads the
        # env var at runtime per llm-client.js:85; credential probes check
        # non-emptiness, not presence). Direct config-only routes are
        # emitted truthfully with their key reference regardless: the
        # selection already carries config_only=True and the runtime
        # evidence stays UNKNOWN until the consumer attempts the route.
        voice_config.update(
            endpoint=rendered["endpoint"],
            model=rendered["wire_model"],
            **(
                {"apiKeyEnv": locator.route.key_reference}
                if locator.route.key_reference
                else {}
            ),
        )


def _gateway_alias(provider, model, key=None):
    """Return an exact configured served alias for a canonical provider/model."""
    try:
        from route_probes import fetch_gateway_models

        auth = resolve_client_auth("LITELLM_OPENCODE_KEY")
        key = key if key is not None else auth.value
        if not key:
            _gateway_alias.last_error = "credential absent"
            return None
        status = []
        models = fetch_gateway_models(
            f"http://127.0.0.1:{_configured_litellm_port() or 4000}/v1",
            key,
            status_out=status,
        )
    except (OSError, ValueError, RuntimeError):
        _gateway_alias.last_error = "gateway unavailable"
        return None
    _gateway_alias.last_error = (
        {
            401: "gateway unauthorized",
            403: "gateway forbidden",
            429: "gateway rate limited",
        }.get(status[0])
        if status
        else None
    )
    wanted = f"{provider}/{model}"
    if wanted in (models or []):
        _gateway_alias.last_error = None
        return wanted
    return None


_gateway_alias.last_error = None


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
    probe_routes=False,
):
    return {
        "local_fallback_preset": local_fallback_preset,
        "role_models": role_models,
        "category_models": category_models,
        "min_reasoning_embedding": min_reasoning_embedding,
        "stt_transport": stt_transport,
        "probe_routes": probe_routes,
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
        selection=(
            CanonicalSelection(
                Operation.CLEANUP_LLM,
                candidates[0][0] if candidates else None,
                candidates[0][1] if candidates else None,
                Qualification.DISCOVERED if candidates else Qualification.UNAVAILABLE,
                "installed local model; config-only, runtime unverified",
                config_only=True,
            )
            if candidates
            else None
        ),
        config_only=True,
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
        help="STT transport policy (omitted preserves saved choice; auto reapplies "
        "the global gateway setting, or the saved/local default when unset)",
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
    probe_routes = options.get("probe_routes", False)
    state = state if state is not None else {}
    saved_transport = state.get("sttTransport")
    gateway_gate_value = os.environ.get("DOTFILES_USE_LITELLM_PROXY")
    gateway_gate = (
        None
        if gateway_gate_value is None
        else gateway_gate_value.lower() in ("1", "true")
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
        "omo-slim-forty-dollars",
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
        # Local Ollama — direct mode needs no API key; gateway mode admits
        # ONLY the served canonical alias + the scoped key (P0.2/P0.3).
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
                    provider = (
                        voice_model.split("/", 1)[0] if "/" in voice_model else "ollama"
                    )
                    model = voice_model.split("/", 1)[-1]
                    candidates = [(provider, voice_model, Qualification.DISCOVERED)]
                    if gateway_gate is True:
                        auth = resolve_client_auth("LITELLM_OPENCODE_KEY")
                        served_alias = _gateway_alias(provider, model, auth.value)
                        if not served_alias or not auth.authorized:
                            logger.warning(
                                _OMITTING_TEMPLATE,
                                Operation.CLEANUP_LLM.value,
                                voice_model,
                                auth.detail or _SERVED_ALIAS_UNAVAILABLE,
                            )
                            unresolved = True
                        else:
                            voice_config = {
                                "endpoint": (
                                    f"http://127.0.0.1:"
                                    f"{_configured_litellm_port() or 4000}/v1"
                                ),
                                "model": served_alias,
                                "apiKeyEnv": "LITELLM_OPENCODE_KEY",
                            }
                            locators[Operation.CLEANUP_LLM.value] = _service_locator(
                                Operation.CLEANUP_LLM,
                                candidates,
                                "gateway",
                                selection=CanonicalSelection(
                                    Operation.CLEANUP_LLM,
                                    provider,
                                    served_alias,
                                    Qualification.DISCOVERED,
                                    "installed local model; config-only, runtime unverified",
                                    config_only=True,
                                ),
                                endpoint=voice_config["endpoint"],
                                wire_model=served_alias,
                                key_reference="LITELLM_OPENCODE_KEY",
                                config_only=True,
                            )
                    else:
                        voice_config = local_voice_provider(voice_model)
                        locators[Operation.CLEANUP_LLM.value] = _service_locator(
                            Operation.CLEANUP_LLM,
                            candidates,
                            "direct",
                            selection=CanonicalSelection(
                                Operation.CLEANUP_LLM,
                                provider,
                                voice_model,
                                Qualification.DISCOVERED,
                                "installed local model; config-only, runtime unverified",
                                config_only=True,
                            ),
                            endpoint=voice_config["endpoint"],
                            wire_model=model,
                            config_only=True,
                        )
            except (OSError, ValueError, RuntimeError) as error:
                logger.warning("Local voice model resolution failed: %s", error)
                unresolved = True
        else:
            unresolved = True
        if "model" not in voice_config:
            unresolved = True

    elif is_pro_tier:
        # Ollama Cloud — gateway mode admits ONLY a served alias + scoped key;
        # a direct route is never installed under the strict contract (P0.2/P0.3).
        if gateway_gate is True:
            auth = resolve_client_auth("LITELLM_OPENCODE_KEY")
            served_alias = _gateway_alias("ollama", _PRO_CLOUD_MODEL, auth.value)
            if not served_alias or not auth.authorized:
                logger.warning(
                    "Omitting voice operation cleanup_llm model gemma4:31b:cloud: %s",
                    auth.detail or _SERVED_ALIAS_UNAVAILABLE,
                )
                unresolved = True
            else:
                voice_config = {
                    "endpoint": (
                        f"http://127.0.0.1:{_configured_litellm_port() or 4000}/v1"
                    ),
                    "model": served_alias,
                    "apiKeyEnv": "LITELLM_OPENCODE_KEY",
                }
                locator = _tier_config_only_locator(
                    Operation.CLEANUP_LLM,
                    "ollama",
                    _PRO_CLOUD_MODEL,
                    voice_config["endpoint"],
                    "LITELLM_OPENCODE_KEY",
                    gateway_gate,
                )
                if locator:
                    locators[Operation.CLEANUP_LLM.value] = locator
        elif can_proxy_cloud:
            voice_config = {
                "endpoint": get_ollama_local_base_url(),
                "model": _PRO_CLOUD_MODEL,
            }
        else:
            voice_config = {
                "endpoint": get_provider_base_url("ollama-cloud"),
                "model": "gemma4:31b",
                "apiKeyEnv": "OLLAMA_API_KEY",
            }
        if gateway_gate is not True:
            locator = _tier_config_only_locator(
                Operation.CLEANUP_LLM,
                "ollama-cloud",
                "gemma4:31b",
                voice_config["endpoint"],
                "OLLAMA_API_KEY",
                gateway_gate,
            )
            if locator:
                locators[Operation.CLEANUP_LLM.value] = locator

    elif tier in ("omo-slim-opencode-zen-free", "free"):
        # These tiers use configured remote providers, never Ollama.
        provider = PROVIDER_ENDPOINTS["opencode"]
        orchestrator = tier_registry.materialize_role_models(
            registry, tier, {}, None
        ).get("orchestrator", "opencode/muse-spark-1.3-contributor-free")
        model = strip_provider_prefix(orchestrator)
        _configure_tier_remote(
            voice_config,
            locators,
            "opencode",
            orchestrator,
            provider["baseUrl"],
            provider["apiKeyEnv"],
            gateway_gate,
        )
    elif is_anthropic_tier:
        # Anthropic — via Meridian proxy or direct
        endpoint = (
            get_meridian_base_url()
            if use_meridian
            else get_provider_base_url("anthropic")
        )
        key_env = "MERIDIAN_API_KEY" if use_meridian else "ANTHROPIC_API_KEY"
        _configure_tier_remote(
            voice_config,
            locators,
            "anthropic",
            "claude-sonnet-5-5",
            endpoint,
            key_env,
            gateway_gate,
        )

    elif is_plus_tier or tier == "pro-plus":
        # OpenAI or Ollama Cloud + OpenAI STT
        endpoint = get_provider_base_url("openai")
        _configure_tier_remote(
            voice_config,
            locators,
            "openai",
            "gpt-6-luna",
            endpoint,
            "OPENAI_API_KEY",
            gateway_gate,
        )

    else:
        # Fallback: Ollama Cloud — gateway mode admits ONLY a served alias +
        # scoped key; a direct route is never installed (P0.2/P0.3).
        if gateway_gate is True:
            auth = resolve_client_auth("LITELLM_OPENCODE_KEY")
            served_alias = _gateway_alias("ollama", _PRO_CLOUD_MODEL, auth.value)
            if not served_alias or not auth.authorized:
                logger.warning(
                    "Omitting voice operation cleanup_llm model gemma4:31b:cloud: %s",
                    auth.detail or _SERVED_ALIAS_UNAVAILABLE,
                )
                unresolved = True
            else:
                voice_config = {
                    "endpoint": (
                        f"http://127.0.0.1:{_configured_litellm_port() or 4000}/v1"
                    ),
                    "model": served_alias,
                    "apiKeyEnv": "LITELLM_OPENCODE_KEY",
                }
                locator = _tier_config_only_locator(
                    Operation.CLEANUP_LLM,
                    "ollama",
                    _PRO_CLOUD_MODEL,
                    voice_config["endpoint"],
                    "LITELLM_OPENCODE_KEY",
                    gateway_gate,
                )
                if locator:
                    locators[Operation.CLEANUP_LLM.value] = locator
        elif can_proxy_cloud:
            voice_config = {
                "endpoint": get_ollama_local_base_url(),
                "model": _PRO_CLOUD_MODEL,
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
        and tier != "pro"
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
    probe_reasons = {}

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
            if probe_routes:
                from route_probes import qualification_from_probe, probe_stt

                probed = []
                gateway_probe_endpoint = (
                    f"http://127.0.0.1:{_configured_litellm_port() or 4000}/v1"
                    if gateway_gate is True
                    else None
                )
                for provider, model_ref, _ in candidates:
                    endpoint, _item = metadata[(provider, model_ref)]
                    key_ref = (
                        "LITELLM_OPENCODE_KEY"
                        if gateway_probe_endpoint
                        else endpoint[1]
                    )
                    if key_ref == "LITELLM_OPENCODE_KEY":
                        key_value = resolve_client_auth(key_ref).value
                    elif key_ref:
                        key_value = os.environ.get(key_ref, "")
                    else:
                        key_value = None
                    result = probe_stt(
                        gateway_probe_endpoint or endpoint[0],
                        strip_provider_prefix(model_ref),
                        key_ref,
                        key_value=key_value,
                    )
                    probed.append(
                        (provider, model_ref, qualification_from_probe(result))
                    )
                    probe_reasons[(provider, model_ref)] = (
                        "; ".join(result.notes) or result.status
                    )
                candidates = probed
            previous = state.get("selections", {}).get(Operation.STT.value)
            selection = _select_voice_candidate(
                Operation.STT, candidates, previously_selected=previous
            )
            if selection.provider is None:
                if selection.qualification is Qualification.UNKNOWN:
                    voice_config["_voice_unresolved"] = True
                return
            endpoint, item = metadata[(selection.provider, selection.model)]
            route_endpoint = (
                f"http://127.0.0.1:{_configured_litellm_port() or 4000}/v1"
                if gateway_gate is True
                else endpoint[0]
            )
            locator = _service_locator(
                Operation.STT,
                candidates,
                "gateway" if gateway_gate else "direct",
                previous,
                selection=selection,
                endpoint=route_endpoint,
                wire_model=strip_provider_prefix(item["name"]),
                key_reference=(
                    "LITELLM_OPENCODE_KEY" if gateway_gate is True else endpoint[1]
                ),
                credential_reference=(
                    "LITELLM_OPENCODE_KEY" if gateway_gate is True else None
                ),
                credential_present=(
                    resolve_client_auth("LITELLM_OPENCODE_KEY").authorized
                    if gateway_gate is True
                    else bool(endpoint[1] and os.environ.get(endpoint[1], "").strip())
                ),
                credential_required=(gateway_gate is True),
            )
            endpoint, item = metadata[
                (locator.selection.provider, locator.selection.model)
            ]
            rendered = _render_locator(locator)
            if rendered:
                voice_config.update(
                    sttEndpoint=rendered["endpoint"], sttModel=rendered["wire_model"]
                )
                if locator.route.key_reference:
                    voice_config["sttApiKeyEnv"] = locator.route.key_reference
                locators[Operation.STT.value] = locator
                if (selection.provider, selection.model) in probe_reasons:
                    locators[Operation.STT.value + "_probe_reason"] = probe_reasons[
                        (selection.provider, selection.model)
                    ]
            else:
                if locator.selection.qualification is Qualification.UNKNOWN:
                    voice_config["_voice_unresolved"] = True
                for key in ("sttEndpoint", "sttModel", "sttApiKeyEnv"):
                    voice_config.pop(key, None)

    if resolved_transport == "openai":
        candidate = [("openai", "whisper-1", Qualification.DISCOVERED)]
        locator = _service_locator(
            Operation.STT,
            candidate,
            ("gateway" if gateway_gate is True else "direct"),
            state.get("selections", {}).get(Operation.STT.value),
            endpoint=(
                f"http://127.0.0.1:{_configured_litellm_port() or 4000}/v1"
                if gateway_gate is True
                else get_provider_base_url("openai")
            ),
            key_reference=(
                "LITELLM_OPENCODE_KEY" if gateway_gate is True else "OPENAI_API_KEY"
            ),
            credential_reference=(
                "LITELLM_OPENCODE_KEY" if gateway_gate is True else None
            ),
            wire_model="whisper-1",
            credential_present=(
                resolve_client_auth("LITELLM_OPENCODE_KEY").authorized
                if gateway_gate is True
                else bool(os.environ.get("OPENAI_API_KEY", "").strip())
            ),
            credential_required=True,
        )
        rendered = _render_locator(locator)
        if rendered:
            voice_config.update(
                sttEndpoint=rendered["endpoint"],
                sttModel=rendered["wire_model"],
            )
            if locator.route.key_reference:
                voice_config["sttApiKeyEnv"] = locator.route.key_reference
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
                    "reason": (
                        probe_reasons.get(
                            (locator.selection.provider, locator.selection.model),
                            locator.selection.reason,
                        )
                        if name == Operation.STT.value
                        else locator.selection.reason
                    ),
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
    if Operation.CLEANUP_LLM.value in locators:
        cleanup = locators[Operation.CLEANUP_LLM.value]
        voice_config["_voice_state"]["selections"][Operation.CLEANUP_LLM.value] = (
            {
                "provider": cleanup.selection.provider,
                "model": cleanup.selection.model,
                "qualification": cleanup.selection.qualification.value,
                "reason": cleanup.selection.reason,
                "transport": cleanup.route.transport.value,
                "endpoint": cleanup.route.endpoint,
                "wire_model": cleanup.route.wire_model,
                **(
                    {"key_reference": cleanup.route.key_reference}
                    if cleanup.route.key_reference
                    else {}
                ),
            }
            if isinstance(cleanup, ServiceLocator)
            else cleanup
        )
    has_stt_state = bool(
        voice_config.get("sttEndpoint") and voice_config.get("sttModel")
    )
    has_llm = bool(voice_config.get("endpoint") and voice_config.get("model"))
    has_config_only_llm = any(
        bool(selection.get("config_only"))
        and selection.get("provider")
        and selection.get("model")
        # A selection-level configuration fact: the key reference surfaces
        # truthfully in the config-only state while the runtime credential
        # evidence stays UNKNOWN (entry attach applies the gateway-only
        # credential gate above).
        for selection in voice_config.get("_voice_state", {})
        .get("selections", {})
        .values()
        if isinstance(selection, dict)
    )
    gateway_tested = any(
        isinstance(item, ServiceLocator)
        and item.route.transport is Transport.GATEWAY
        and not item.selection.config_only
        for item in locators.values()
    )
    if unresolved and not has_llm and not has_config_only_llm:
        voice_status = "unresolved"
    elif (has_llm or has_config_only_llm) and not gateway_tested:
        # A config-only route is a configuration contract, not a tested
        # gateway operation (B3 contract) — it must not be labelled as a
        # runtime 'partial' delivery just because endpoint/model keys exist,
        # and a config-only selection is not fully 'unresolved' either: its
        # key reference is a truthful configuration fact while the runtime
        # evidence stays UNKNOWN.
        voice_status = "config-only"
    elif has_llm and not has_stt_state:
        voice_status = "partial"
    elif not has_llm and not has_stt_state:
        voice_status = "unresolved"
    else:
        voice_status = "configured"
    voice_config["_voice_status"] = voice_status
    if unresolved and is_local_tier:
        voice_config["_voice_unresolved"] = True
        voice_config["_voice_state"]["selections"].pop(
            Operation.CLEANUP_LLM.value, None
        )
        voice_config["_voice_state"]["selections"].pop(Operation.STT.value, None)
    if not voice_config["_voice_state"]["selections"] and unresolved:
        voice_config["_voice_state"] = None
    if unresolved and no_local_models and not locators:
        return {
            "_voice_unresolved": True,
            "_voice_state": None,
            "_voice_status": "unresolved",
        }
    return voice_config


def main():
    voice_specs = active_cli_plugin_specs()
    if len(voice_specs) != 1:
        logger.critical("Expected exactly one active voice CLI plugin spec")
        return 1
    voice_plugin_spec = voice_specs[0]
    parser = argparse.ArgumentParser(
        description="Configure OpenCode voice plugin (cli.json) based on active tier."
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
        "--probe-routes",
        action="store_true",
        help="Explicitly probe local voice routes before qualification",
    )
    parser.add_argument(
        "--show-secrets",
        action="store_true",
        help="Include API key env var names in dry-run output (keys are never printed)",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip backup of existing cli.json",
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
    voice_config = get_voice_config(
        args.preset,
        _voice_resolution_options(
            args.local_fallback_preset,
            args.role_models,
            args.category_models,
            args.min_reasoning_embedding,
            args.stt_transport,
            args.probe_routes,
        ),
        state=prior_state,
    )
    voice_state = voice_config.pop("_voice_state", None)
    unresolved = voice_config.pop("_voice_unresolved", False)
    voice_status_marker = voice_config.pop("_voice_status", None)

    output_path = os.path.join(config_dir_path, "cli.json")
    cli_root = []
    cli_root_form = "array"
    if os.path.exists(output_path):
        try:
            with open(output_path, "r", encoding="utf-8") as config_file:
                cli_root = json.load(config_file)
            # Upstream v2 consumes both roots (probe-verified 2026-10-31):
            # an ordered entry array and a {"plugins": [...]} object form.
            if isinstance(cli_root, list):
                cli_config = cli_root
                cli_root_form = "array"
            elif isinstance(cli_root, dict) and isinstance(
                cli_root.get("plugins", []), list
            ):
                cli_config = cli_root["plugins"]
                cli_root_form = "object"
            else:
                raise ValueError(
                    "cli.json root must be an array or a {plugins: [...]} object"
                )
        except (OSError, json.JSONDecodeError, ValueError) as error:
            logger.critical(f"Could not read existing cli.json safely: {error}")
            return 1
    else:
        cli_config = []
    filtered_plugins = [
        entry for entry in cli_config if not _voice_entry_matches(entry)
    ]
    existing_voice = next(
        (entry for entry in cli_config if _voice_entry_matches(entry)),
        None,
    )
    # When cli.json has no voice entry yet, the previously managed tui.json
    # route seeds the v2 entry: the package maps onto the current manifest
    # spec and its options are preserved verbatim.
    legacy_options = None
    if existing_voice is None:
        try:
            with open(
                os.path.join(config_dir_path, "tui.json"), encoding="utf-8"
            ) as tui_file:
                legacy_tui = json.load(tui_file)
            if isinstance(legacy_tui, dict) and isinstance(
                legacy_tui.get("plugin", []), list
            ):
                legacy_entry = next(
                    (
                        entry
                        for entry in legacy_tui["plugin"]
                        if _voice_tui_entry_matches(entry)
                    ),
                    None,
                )
                if legacy_entry is not None:
                    if isinstance(legacy_entry, dict):
                        legacy_options = legacy_entry.get("options")
                    elif (
                        isinstance(legacy_entry, (list, tuple))
                        and len(legacy_entry) > 1
                    ):
                        legacy_options = legacy_entry[1]
                    if isinstance(legacy_options, str):
                        try:
                            legacy_options = json.loads(legacy_options)
                        except json.JSONDecodeError:
                            legacy_options = None
                    if not isinstance(legacy_options, dict):
                        legacy_options = None
        except (OSError, json.JSONDecodeError, ValueError) as error:
            logger.debug("No usable legacy tui.json voice route: %s", error)
    entry_options = {
        key: value
        for key, value in voice_config.items()
        if key
        in {"endpoint", "model", "apiKeyEnv", "sttEndpoint", "sttModel", "sttApiKeyEnv"}
    }
    existing_options = (
        existing_voice.get("options", {}) if isinstance(existing_voice, dict) else {}
    )
    route_installed = False
    if (
        not (voice_config.get("endpoint") and voice_config.get("model"))
        and existing_voice is not None
    ):
        filtered_plugins.append(existing_voice)
        route_installed = True
    elif not (voice_config.get("endpoint") and voice_config.get("model")) and (
        legacy_options is not None
    ):
        # The legacy v1 route's working selection maps onto the current
        # manifest spec, options preserved verbatim.
        filtered_plugins.append(
            {"package": voice_plugin_spec, "options": legacy_options}
        )
        route_installed = True
    elif unresolved:
        if existing_voice is not None:
            for key in ("endpoint", "model", "apiKeyEnv"):
                if key in existing_options:
                    entry_options[key] = existing_options[key]
            filtered_plugins.append(
                {"package": voice_plugin_spec, "options": entry_options}
            )
            route_installed = True
        # Unresolved without anything preserved: do not register a route.
    elif voice_config.get("endpoint") and voice_config.get("model"):
        filtered_plugins.append(
            {"package": voice_plugin_spec, "options": entry_options}
        )
        route_installed = True

    # Dry-run mode
    if args.dry_run:
        print("# Dry-run: proposed voice configuration")
        print(f"# Tier: {args.preset}")
        print(f"# Config dir: {config_dir_path}")
        print()
        if unresolved:
            print("Voice outcome: unresolved; existing selection preserved")
        elif not existing_voice or existing_voice != {
            "package": voice_plugin_spec,
            "options": entry_options,
        }:
            print(
                json.dumps(
                    {"package": voice_plugin_spec, "options": entry_options}, indent=2
                )
            )
        changed_names = sorted(
            key
            for key in entry_options
            if not existing_voice or existing_options.get(key) != entry_options[key]
        )
        print(f"Changed setting names: {', '.join(changed_names) or 'none'}")
        preserved_names = [
            str(entry.get("package", "")) if isinstance(entry, dict) else str(entry)
            for entry in cli_config
            if not _voice_entry_matches(entry)
        ]
        print(f"Preserved plugin names: {', '.join(preserved_names) or 'none'}")
        return

    # Write cli.json
    os.makedirs(config_dir_path, exist_ok=True)
    output_path = os.path.join(config_dir_path, "cli.json")

    # Backup existing config
    if os.path.exists(output_path) and not args.no_backup:
        backup_path = backup_file(output_path, enabled=True)
        if backup_path:
            logger.info(f"Backed up existing cli.json to {backup_path}")

    try:
        if cli_root_form == "object":
            cli_root["plugins"] = filtered_plugins
            cli_payload = json.dumps(cli_root, indent=2) + "\n"
        else:
            cli_payload = json.dumps(filtered_plugins, indent=2) + "\n"
        _atomic_private_write(output_path, cli_payload)
        logger.info(f"cli.json written to {output_path}")
    except Exception as e:
        logger.critical(f"Failed to write cli.json: {e}")
        sys.exit(1)

    tui_path = os.path.join(config_dir_path, "tui.json")
    if route_installed and os.path.exists(tui_path):
        try:
            with open(tui_path, encoding="utf-8") as tui_file:
                tui_config = json.load(tui_file)
            if isinstance(tui_config, dict) and isinstance(
                tui_config.get("plugin", []), list
            ):
                tui_plugins = tui_config.get("plugin", [])
                remaining = [
                    entry
                    for entry in tui_plugins
                    if not _voice_tui_entry_matches(entry)
                ]
                if remaining != tui_plugins:
                    tui_config["plugin"] = remaining
                    if not args.no_backup:
                        backup_file(tui_path, enabled=True)
                    write_text_file(
                        tui_path, json.dumps(tui_config, indent=2) + "\n", backup=False
                    )
        except (OSError, json.JSONDecodeError, ValueError) as error:
            logger.warning(
                "Could not remove managed voice entry from tui.json: %s", error
            )

    if voice_state:
        _save_voice_state(config_dir_path, voice_state)

    # Summary
    whisper_model = os.environ.get("DOTFILES_WHISPER_MODEL", "ggml-large-v3-turbo.bin")
    piper_voice = os.environ.get("DOTFILES_PIPER_VOICE", "en_US-lessac-high")
    # Mirror the status predicate: an incomplete STT route (endpoint without
    # a model) must not present itself as configured detail in the summary.
    has_stt = bool(voice_config.get("sttEndpoint") and voice_config.get("sttModel"))

    voice_status = voice_status_marker or ("unresolved" if unresolved else "configured")
    summary_lines = [
        (
            "OpenCode voice plugin partially configured!"
            if voice_status in ("partial", "unresolved")
            else "OpenCode voice plugin configured!"
        ),
        "",
        f"Config written to: {output_path}",
    ]
    outcome_lines = {
        "unresolved": "Voice outcome: unresolved; no plugin route installed",
        "partial": "Voice outcome: partial; LLM/STT cleanup incomplete",
        "local-only": "Voice outcome: partial; local Whisper STT configuration only",
        "config-only": (
            "Voice outcome: config-only; runtime unverified (local Whisper/Piper or curated route)"
        ),
    }
    if voice_status in outcome_lines:
        summary_lines.append(outcome_lines[voice_status])
    elif voice_config.get("endpoint") and voice_config.get("model"):
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
