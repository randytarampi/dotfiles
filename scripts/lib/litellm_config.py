"""Pure LiteLLM proxy configuration generation from dotfiles registries."""

from __future__ import annotations

import json
import math
import logging
import os
import re
import stat
import time
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import constants
from constants import (
    get_meridian_base_url,
    get_ollama_local_base_url,
    is_meridian_configured,
)
from discover_models import list_cloud_ollama_models
from ai_models import is_known_unqualified_tts, is_specialized_model_name
from local_engines import (
    active_engines,
    iter_engine_models,
    local_endpoint_for,
    _ollama_metadata,
)
from provider_endpoints import PROVIDER_ENDPOINTS, provider_models
from litellm_routing import routing_entries_are_safe
from litellm_aliases import canonical_allowlist_key  # noqa: E402 -- lib sibling import.
from model_catalogues import open_same_origin

import models_dev  # noqa: E402 -- lib sibling import.

# Fetch seam for the models.dev rate index; tests stub this module attribute.
_MODELS_DEV_FETCH = models_dev.fetch_models_dev

logger = logging.getLogger(__name__)

OPENAI_SERVICE_LANES_ENV = "DOTFILES_USE_OPENAI_SERVICE_LANES"
LITELLM_SETUP_GATE = "DOTFILES_RUN_LITELLM_SETUP=1"
OPENAI_SERVICE_LANE_MODELS = {"gpt-6.1-sol", "gpt-6-astra", "gpt-6-luna"}

# Cloud providers whose live /v1/models catalogue the generator enumerates
# into the model list. Non-enumerable providers (openai keyless, opencode
# 403) keep their single curated default alias instead.
LIVE_CATALOGUE_PROVIDERS = (
    "google",
    "openrouter",
    "ollama-cloud",
    "cerebras",
    "cohere",
    "poolside",
    "huggingface",
    "openai",
)
last_generation_notes = []
COHERE_CATALOGUE_MAX_PAGES = 20
GOOGLE_CATALOGUE_MAX_PAGES = 10

# Upstream per-id pricing captured during catalogue fetches (id →
# (prompt, completion) USD/token strings). The emission loop turns OpenRouter
# pricing strings into model_info cost rates; other providers stay unpriced.
_LIVE_CATALOGUE_PRICES: dict[str, dict[str, tuple[str, str]]] = {}

# Lazy snapshot of the installed LiteLLM bundled cost map. Reading the same
# file the upstream service uses keeps custom rates authoritative rather than
# invented; a missing map leaves rows honestly unpriced.
BUILTIN_COST_MAP: dict = {}
_BUILTIN_COST_MAP_LOADED = False


def _builtin_cost_map(environ=None):
    """Load cost rates once per process.

    The checked-in snapshot (configs/litellm/model-rates.json) wins: it covers
    wire families the installed package's bundled backup file lacks (claude-*
    entries are absent there). The installed file is the fallback so the rest
    of the map (gemini, gpt, …) prices without snapshotting everything. A
    missing map leaves rows honestly unpriced.
    """
    global BUILTIN_COST_MAP, _BUILTIN_COST_MAP_LOADED
    if _BUILTIN_COST_MAP_LOADED:
        return BUILTIN_COST_MAP
    _BUILTIN_COST_MAP_LOADED = True
    snapshot_path = (
        Path(__file__).resolve().parents[2] / "configs/litellm/model-rates.json"
    )
    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        if isinstance(snapshot, dict) and isinstance(snapshot.get("models"), dict):
            BUILTIN_COST_MAP = {
                str(key): value
                for key, value in snapshot["models"].items()
                if isinstance(value, dict)
            }
    except (OSError, json.JSONDecodeError):
        pass
    root = os.environ.get(
        "LITELLM_ROOT", str(Path("~/.local/share/litellm").expanduser())
    )
    matches = sorted(
        Path(root).glob(
            "venv/lib/python3*/site-packages/litellm/"
            "model_prices_and_context_window_backup.json"
        )
    )
    if matches:
        try:
            data = json.loads(matches[0].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, dict):
                    BUILTIN_COST_MAP.setdefault(str(key), value)
    return BUILTIN_COST_MAP


def builtin_rates(wire_model):
    """Return model_info rates for a wire model from the installed cost map.

    Looks up the namespaced key first (e.g. 'gemini/gemini-3.8-flash'), then
    the provider-stripped bare key (e.g. 'claude-sonnet-5-5', which the
    bundled maps only alias bare). Subscription rows (wire 'chatgpt/…', token
    bundles billed via a plan, not per token) are never priced at API rates.
    """
    if not isinstance(wire_model, str) or "/" not in wire_model:
        return None
    if wire_model.startswith("chatgpt/"):
        return None
    provider, model_id = wire_model.split("/", 1)
    cost_map = _builtin_cost_map()
    entry = cost_map.get(wire_model) or cost_map.get(model_id)
    if not isinstance(entry, dict):
        return None
    input_rate = entry.get("input_cost_per_token")
    output_rate = entry.get("output_cost_per_token")
    if not isinstance(input_rate, (int, float)) or not isinstance(
        output_rate, (int, float)
    ):
        return None
    info: dict[str, object] = {
        "input_cost_per_token": float(input_rate),
        "output_cost_per_token": float(output_rate),
    }
    for source_key, info_key in (
        ("cache_read_input_token_cost", "cache_read_input_token_cost"),
        ("cache_creation_input_token_cost", "cache_creation_input_token_cost"),
        ("max_input_tokens", "max_input_tokens"),
        ("max_output_tokens", "max_output_tokens"),
        ("mode", "mode"),
    ):
        value = entry.get(source_key)
        if isinstance(value, (int, float, str)) and value is not None:
            info[info_key] = value
    return info


MODELS_DEV_WIRE_PROVIDERS = {
    "meridian": "anthropic",
    "anthropic": "anthropic",
    "google": "google",
    "openai": "openai",
    "chatgpt": "openai",
    "ollama-cloud": "ollama-cloud",
    "ollama": "ollama-cloud",
    "opencode": "opencode",
    "github-copilot": "github-copilot",
}

_MODELS_DEV_RATES: dict = {}
_MODELS_DEV_RATES_LOADED = False


def _models_dev_rates(environ=None):
    """Lazily build per-provider rates from models.dev ($/token units).

    models.dev lists costs per million tokens; LiteLLM wants per-token, so
    values divide by 1e6. Uses the shared 24h-cached fetch and degrades to
    an empty index on failure so generation never breaks on metadata.
    """
    global _MODELS_DEV_RATES, _MODELS_DEV_RATES_LOADED
    if _MODELS_DEV_RATES_LOADED:
        return _MODELS_DEV_RATES
    _MODELS_DEV_RATES_LOADED = True
    try:
        catalogue = _MODELS_DEV_FETCH()
    except Exception:
        catalogue = {}
    if not isinstance(catalogue, dict):
        catalogue = {}
    rates: dict[str, dict] = {}
    for provider in models_dev.SUPPORTED_PROVIDERS:
        entries = (catalogue.get(provider) or {}).get("models")
        if not isinstance(entries, dict):
            continue
        provider_rates = {}
        for model_id, entry in entries.items():
            if isinstance(model_id, str) and model_id:
                provider_rates[model_id] = entry if isinstance(entry, dict) else {}
        rates[provider] = provider_rates
    _MODELS_DEV_RATES = rates
    return _MODELS_DEV_RATES


def _models_dev_rate_entry(entry):
    """Convert one models.dev model entry into LiteLLM model_info rates."""
    cost = entry.get("cost") if isinstance(entry, dict) else None
    if not isinstance(cost, dict):
        return None
    input_rate = cost.get("input")
    output_rate = cost.get("output")
    if not isinstance(input_rate, (int, float)) or not isinstance(
        output_rate, (int, float)
    ):
        return None
    info: dict[str, object] = {
        "input_cost_per_token": float(input_rate) / 1e6,
        "output_cost_per_token": float(output_rate) / 1e6,
    }
    for source_key, info_key in (
        ("cache_read", "cache_read_input_token_cost"),
        ("cache_write", "cache_creation_input_token_cost"),
    ):
        value = cost.get(source_key)
        if isinstance(value, (int, float)):
            info[info_key] = float(value) / 1e6
    limit = entry.get("limit") if isinstance(entry, dict) else None
    if isinstance(limit, dict):
        if isinstance(limit.get("context"), (int, float)) and limit["context"] > 0:
            info["max_input_tokens"] = int(limit["context"])
        if isinstance(limit.get("output"), (int, float)) and limit["output"] > 0:
            info["max_output_tokens"] = int(limit["output"])
    return info


def model_rates(provider, model_id, wire_model):
    """Rates for one emitted row: models.dev first, installed map as fallback.

    ChatGPT-transport rows (subscription lanes) deliberately use the OpenAI
    API rates as an estimate — the plan bundle has no per-token price, and
    the user wants usage pages to show a comparable number for every lane.
    """
    dev_provider = MODELS_DEV_WIRE_PROVIDERS.get(provider)
    if dev_provider:
        candidates = [model_id]
        if provider == "ollama":
            # Daemon cloud stubs spell their suffix two ways upstream;
            # match whichever form models.dev carries, never blind-strip.
            for suffix in (":cloud", "-cloud"):
                if model_id.endswith(suffix):
                    candidates.append(model_id[: -len(suffix)])
        for dev_id in candidates:
            info = _models_dev_rate_entry(
                _models_dev_rates().get(dev_provider, {}).get(dev_id)
            )
            if info:
                return info
    return builtin_rates(wire_model)


def build_rates_snapshot(environ=None):
    """Generate the models.dev-derived rates document for cost-map merging.

    Keys are bare model ids (the shape request-time cost resolution looks up
    regardless of the wire namespace, verified for claude/gemini/gpt wires).
    OpenAI ids also emit `chatgpt/<id>` twins: the installed bundled map has
    namespaced chatgpt entries with deliberately-empty (subscription) rates,
    and those shadow the bare key at request-time resolution — pricing the
    twin makes the subscription-lane estimate actually record.
    """
    rates = {}
    for provider, entries in _models_dev_rates(environ).items():
        for model_id, entry in entries.items():
            info = _models_dev_rate_entry(entry)
            if info:
                rates[model_id] = info
                if provider == "openai":
                    # Protocol metadata controls LiteLLM's chat-to-Responses
                    # bridge; a rates-only twin incorrectly uses the upstream
                    # chat endpoint instead of the subscription Responses API.
                    rates[f"chatgpt/{model_id}"] = {
                        **info,
                        "mode": "responses",
                        "litellm_provider": "chatgpt",
                    }
                elif provider == "ollama-cloud":
                    # Daemon cloud-stub requests carry ':cloud'/-cloud
                    # suffix spellings (with and without the ollama/
                    # namespace); the bundled map never prices either form,
                    # so emit priced twins for every stub spelling a client
                    # may request.
                    for suffix in (":cloud", "-cloud"):
                        rates[f"{model_id}{suffix}"] = info
                        rates[f"ollama/{model_id}{suffix}"] = info
    # Provenance stamps the cache's data vintage (deterministic inside the
    # 24h cache window); empty when the fetch degraded.
    fetched = ""
    try:
        fetched = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(os.path.getmtime(models_dev.CACHE_FILE)),
        )
    except OSError:
        pass
    return {
        "_provenance": {"source": "models.dev api.json", "fetched": fetched},
        "models": rates,
    }


def openrouter_pricing_for(model_id):
    """Return model_info cost rates for a raw (canonicalized) OpenRouter id.

    Sidecar keys hold the raw upstream spelling, which keeps the ':free'
    suffix that canonicalization strips; try the spelled and suffix-dropped
    forms. Rates return only when both prompt and completion parse."""
    rates = _LIVE_CATALOGUE_PRICES.get("openrouter", {})
    for candidate in (model_id, model_id + ":free"):
        pricing = rates.get(candidate)
        if pricing:
            break
    else:
        return None
    try:
        return {
            "input_cost_per_token": float(pricing[0]),
            "output_cost_per_token": float(pricing[1]),
        }
    except (TypeError, ValueError):
        return None


# Per-client virtual keys provisioned in the LiteLLM proxy (alias → env var
# name persisted in service.env). Shared source for configure-litellm.py
# (provisioning) and verify-config.py (doctor allowlist).
APP_KEYS = {
    "opencode": "LITELLM_OPENCODE_KEY",
    "pi": "LITELLM_PI_KEY",
    "openwebui": "LITELLM_OPENWEBUI_KEY",
    "junie": "LITELLM_JUNIE_KEY",
}

# Descriptive inventory only; provisioning and consumer wiring remain owned by
# their existing configure/generate scripts.
_ELIGIBILITY = {
    "setup_gate": "DOTFILES_RUN_LITELLM_SETUP=1",
    "installed": "client installation is present",
    "user_enabled": "explicit user opt-in is recorded",
    "verified_usable_transport": "supported transport is verified usable",
}


def _client_row(
    client,
    env_name,
    signatures,
    transports,
    owner,
    gates,
    auth,
    read_path,
    classification,
    credential,
    status=None,
    eligibility=None,
    evidence=(),
):
    return {
        "client": client,
        "env_name": env_name,
        "key_file": f"~/.local/share/litellm/clients/{client}.key",
        "install_signatures": signatures,
        "transports": transports,
        "config_owner": owner,
        "setup_gates": gates,
        "auth_source": auth,
        "consumer_read_path": read_path,
        "classification": classification,
        "credential_policy": credential,
        "eligibility": dict(eligibility or _ELIGIBILITY),
        "classification_status": status or classification,
        "evidence": list(evidence) or ["scripts/lib/litellm_config.py:CLIENT_REGISTRY"],
    }


CLIENT_REGISTRY = [
    _client_row(
        "opencode",
        APP_KEYS["opencode"],
        ["opencode CLI installed"],
        ["OpenAI-compatible"],
        "scripts/configure-opencode.py",
        [LITELLM_SETUP_GATE],
        "file-or-env",
        "scripts/lib/litellm_client_auth.py",
        "eligible",
        "provision",
    ),
    _client_row(
        "pi",
        APP_KEYS["pi"],
        ["pi CLI installed"],
        ["OpenAI-compatible"],
        "scripts/configure-pi.py",
        ["DOTFILES_USE_LITELLM_PROXY=1", LITELLM_SETUP_GATE],
        "file-or-env",
        "scripts/configure-pi.py provider config",
        "eligible",
        "provision",
    ),
    _client_row(
        "openwebui",
        APP_KEYS["openwebui"],
        ["Open WebUI installed"],
        ["OpenAI-compatible"],
        "scripts/configure-openwebui.py",
        [LITELLM_SETUP_GATE],
        "file-or-env",
        "Open WebUI connection settings",
        "eligible",
        "provision",
    ),
    _client_row(
        "junie",
        APP_KEYS["junie"],
        ["JetBrains IDE with Junie"],
        ["OpenAI-compatible"],
        "scripts/generate-jetbrains-profiles.py",
        [LITELLM_SETUP_GATE],
        "file",
        "~/.local/share/litellm/clients/junie.key",
        "eligible",
        "provision; derived twins use --groups-json",
    ),
    _client_row(
        "codex",
        "LITELLM_CODEX_KEY",
        ["Codex CLI installed"],
        ["Responses API"],
        "scripts/configure-codex.py managed block",
        ["DOTFILES_RUN_CODEX_SETUP=1"],
        "file-or-env",
        "~/.codex/config.toml [model_providers.litellm]",
        "pending-qualification",
        "provision-once-verified",
        "D1a",
    ),
    _client_row(
        "claude",
        "LITELLM_CLAUDE_KEY",
        ["Claude CLI installed"],
        ["Anthropic Messages at gateway root"],
        "pending",
        ["DOTFILES_RUN_CLAUDE_SETUP=1"],
        "file-or-env",
        "Claude gateway configuration",
        "pending-qualification",
        "pending",
        "D1c",
    ),
    _client_row(
        "jetbrains",
        "LITELLM_JETBRAINS_KEY",
        ["JetBrains IDE installed"],
        ["OpenAI-compatible"],
        "scripts/generate-jetbrains-profiles.py",
        ["DOTFILES_RUN_JETBRAINS_SETUP=1"],
        "file-or-env",
        "generated JetBrains profile",
        "pending-qualification",
        "pending",
        "D2a; needs F frozen snapshot",
    ),
    _client_row(
        "vscode",
        "not-applicable",
        ["VS Code installed"],
        ["extension-dependent"],
        "pending",
        ["DOTFILES_RUN_VSCODE_SETUP=1"],
        "env",
        "VS Code extension settings",
        "classification-only",
        "no-credential",
        "D2b real-session qualification",
    ),
    _client_row(
        "copilot",
        "not-applicable",
        ["GitHub Copilot extension installed"],
        ["extension-dependent"],
        "pending",
        ["DOTFILES_RUN_COPILOT_SETUP=1"],
        "env",
        "Copilot extension settings",
        "classification-only",
        "no-credential",
        "D2c real-session qualification",
    ),
    _client_row(
        "cursor",
        "not-applicable",
        ["Cursor installed"],
        ["loopback-unroutable"],
        "docs/UNMANAGED-CLIENTS.md",
        ["explicit separate keys-only approval required"],
        "none",
        "No supported LiteLLM consumer path",
        "classification-only",
        "no-credential",
        "classification-only; not-routable",
    ),
    _client_row(
        "gemini",
        "LITELLM_GEMINI_KEY",
        ["Gemini CLI installed"],
        ["pending protocol verification"],
        "pending",
        ["DOTFILES_RUN_GEMINI_SETUP=1"],
        "file-or-env",
        "Gemini CLI configuration",
        "pending-qualification",
        "pending",
        "D3 verify-installed-version-protocol gate",
    ),
    _client_row(
        "hermes",
        "LITELLM_HERMES_KEY",
        ["Hermes installed"],
        ["pending"],
        "none",
        ["DOTFILES_RUN_HERMES_SETUP=1"],
        "file-or-env",
        "No current ~/.hermes/config.yaml manager",
        "pending-qualification",
        "pending",
        "D4",
    ),
    _client_row(
        "mozart",
        "not-applicable",
        ["Mozart router configured"],
        ["GenericOpenAI gateways"],
        "scripts/configure-mozart-router.py",
        ["DOTFILES_USE_LITELLM_PROXY=1"],
        "provider-specific api_key_env",
        "standard client-adapter path; provider keys only when env set at generation",
        "eligible",
        "no-credential",
        "existing-consumer",
        evidence=[
            "scripts/configure-mozart-router.py:46",
            "scripts/configure-mozart-router.py:62",
            "scripts/lib/litellm_routing.py:25",
            "docs/MOZART.md:7",
        ],
    ),
]


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


def _entry(
    alias,
    model,
    *,
    api_base=None,
    key_env=None,
    key_literal=None,
    alias_kind=None,
    model_info=None,
    service_tier=None,
):
    params = {"model": model}
    if api_base:
        params["api_base"] = api_base
    if key_env:
        params["api_key"] = f"os.environ/{key_env}"
    elif key_literal:
        params["api_key"] = key_literal
    if model_info:
        params["model_info"] = model_info
    if service_tier:
        params["service_tier"] = service_tier
    return {
        "model_name": alias,
        "litellm_params": params,
        "_alias_kind": alias_kind or ("bare" if "/" not in alias else "qualified"),
        "_upstream_ref": (api_base or "", model),
    }


def oauth_cache_path(provider, environ=None):
    """Resolve the installed LiteLLM OAuth cache file, including its overrides."""
    environ = os.environ if environ is None else environ
    home = Path(environ.get("HOME", str(Path.home()))).expanduser()
    if provider == "github_copilot":
        directory = Path(
            environ.get(
                "GITHUB_COPILOT_TOKEN_DIR", str(home / ".config/litellm/github_copilot")
            )
        ).expanduser()
        return directory / environ.get("GITHUB_COPILOT_API_KEY_FILE", "api-key.json")
    if provider == "chatgpt":
        directory = Path(
            environ.get("CHATGPT_TOKEN_DIR", str(home / ".config/litellm/chatgpt"))
        ).expanduser()
        return directory / environ.get("CHATGPT_AUTH_FILE", "auth.json")
    raise ValueError(f"unsupported OAuth provider: {provider}")


def oauth_cache_expiry(provider, environ=None):
    """Return expiry timestamp for a parseable, unexpired OAuth cache."""
    try:
        payload = json.loads(
            oauth_cache_path(provider, environ).read_text(encoding="utf-8")
        )
        if not isinstance(payload, dict):
            return None
        token_fields = (
            ("token",)
            if provider == "github_copilot"
            else ("access_token", "refresh_token")
        )
        if any(
            not isinstance(payload.get(field), str) or not payload[field].strip()
            for field in token_fields
        ):
            return None
        expires = payload.get("expires_at")
        if isinstance(expires, bool) or not isinstance(expires, (int, float)):
            return None
        expires = float(expires)
        if not math.isfinite(expires):
            return None
        if expires <= time.time() + (60 if provider == "chatgpt" else 0):
            return None
        return expires
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def chatgpt_verified_openai_models(environ=None):
    """Read the mode-600 IDs verified through a supervised ChatGPT session."""
    environ = os.environ if environ is None else environ
    home = Path(environ.get("HOME", str(Path.home()))).expanduser()
    path = home / ".local/share/litellm/chatgpt_verified_models.json"
    try:
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) != 0o600:
            return set()
        values = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return set()
    if not isinstance(values, list) or any(
        not isinstance(value, str) or not value or "/" in value for value in values
    ):
        return set()
    return set(values)


def _google_catalogue_rows(payload):
    """Extract (entries, id_field, next_token_field) from a Google catalogue page.

    Google serves two shapes: the OpenAI-compatible endpoint returns
    ``{"data": [{"id": ...}], "nextPageToken": ...}`` while the native API
    returns ``{"models": [{"name": "models/<id>"}], "next_page_token": ...}``.
    """
    if not isinstance(payload, dict):
        return None, None, None
    if isinstance(payload.get("data"), list):
        return payload["data"], "id", "nextPageToken"
    if isinstance(payload.get("models"), list):
        return payload["models"], "name", "next_page_token"
    return None, None, None


def _live_catalogue(provider, key, base_url, timeout=10):
    """Fetch a provider's live /models IDs, failing closed when unreachable."""
    # bandit B310: provider catalogues are https-only; validate before the
    # request is constructed so urlopen can never see a custom scheme.
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        # bandit B310: audit the constructed URL before urlopen sees it.
        raise LiveCatalogueError(f"refusing non-https catalogue url for {provider}")
    catalogue_path = parsed.path.rstrip("/")
    if provider in ("cohere", "huggingface") and not catalogue_path:
        catalogue_path = "/v1"
    if provider == "cohere":
        model_ids = set()
        page_token = None
        seen_tokens = set()
        for page_number in range(COHERE_CATALOGUE_MAX_PAGES):
            query: dict[str, str | int] = {"page_size": 1000}
            if page_token:
                query["page_token"] = page_token
            url = urllib.parse.urlunsplit(
                (
                    parsed.scheme,
                    parsed.netloc,
                    catalogue_path + "/models",
                    urllib.parse.urlencode(query),
                    "",
                )
            )
            request = urllib.request.Request(
                url,
                headers={
                    "Authorization": f"Bearer {key}",
                    "User-Agent": "dotfiles-catalogue/1.0",
                },
            )
            try:
                with open_same_origin(request, timeout=timeout) as response:
                    payload = json.load(response)
            except (
                urllib.error.URLError,
                ValueError,
                TypeError,
                AttributeError,
            ) as error:
                raise LiveCatalogueError(
                    f"live model catalogue enumeration failed for {provider}: {error}"
                ) from error
            if not isinstance(payload, dict) or not isinstance(
                payload.get("models"), list
            ):
                raise LiveCatalogueError(
                    "malformed Cohere catalogue: expected models list"
                )
            for item in payload["models"]:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("name"), str)
                    or not isinstance(item.get("endpoints", []), list)
                ):
                    raise LiveCatalogueError("malformed Cohere catalogue model entry")
                if "chat" in item.get("endpoints", []) and not item.get(
                    "is_deprecated", False
                ):
                    model_ids.add(item["name"])
            next_token = payload.get("next_page_token")
            if next_token is not None and not isinstance(next_token, str):
                raise LiveCatalogueError(
                    "malformed Cohere catalogue: next_page_token must be a string"
                )
            if not next_token:
                return sorted(model_ids)
            if next_token in seen_tokens:
                raise LiveCatalogueError("Cohere catalogue repeated next_page_token")
            seen_tokens.add(next_token)
            if page_number + 1 >= COHERE_CATALOGUE_MAX_PAGES:
                raise LiveCatalogueError(
                    f"Cohere catalogue exceeded {COHERE_CATALOGUE_MAX_PAGES} pages"
                )
            page_token = next_token
    if provider == "google":
        model_ids = set()
        page_token = None
        seen_tokens = set()
        for page_number in range(GOOGLE_CATALOGUE_MAX_PAGES):
            # The OpenAI-compatible endpoint rejects pageSize ("Unknown
            # name"); fetch page 1 plain and forward only a real token.
            query: dict[str, str | int] = {}
            if page_token:
                query["pageToken"] = page_token
            url = urllib.parse.urlunsplit(
                (
                    parsed.scheme,
                    parsed.netloc,
                    catalogue_path + "/models",
                    urllib.parse.urlencode(query),
                    "",
                )
            )
            request = urllib.request.Request(
                url,
                headers={
                    "Authorization": f"Bearer {key}",
                    "User-Agent": "dotfiles-catalogue/1.0",
                },
            )
            try:
                with open_same_origin(request, timeout=timeout) as response:
                    payload = json.load(response)
            except (
                urllib.error.URLError,
                ValueError,
                TypeError,
                AttributeError,
            ) as error:
                raise LiveCatalogueError(
                    f"live model catalogue enumeration failed for {provider}: {error}"
                ) from error
            entries, id_field, next_field = _google_catalogue_rows(payload)
            if entries is None:
                raise LiveCatalogueError(
                    "malformed Google catalogue: expected data or models list"
                )
            for item in entries:
                if not isinstance(item, dict) or not isinstance(
                    item.get(id_field), str
                ):
                    raise LiveCatalogueError("malformed Google catalogue model entry")
                model_ids.add(item[id_field])
            next_token = payload.get(next_field)
            if next_token is not None and not isinstance(next_token, str):
                raise LiveCatalogueError(
                    "malformed Google catalogue: nextPageToken must be a string"
                )
            if not next_token:
                return sorted(model_ids)
            if next_token in seen_tokens:
                raise LiveCatalogueError("Google catalogue repeated nextPageToken")
            seen_tokens.add(next_token)
            if page_number + 1 >= GOOGLE_CATALOGUE_MAX_PAGES:
                raise LiveCatalogueError(
                    f"Google catalogue exceeded {GOOGLE_CATALOGUE_MAX_PAGES} pages"
                )
            page_token = next_token
    url = urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, catalogue_path + "/models", "", "")
    )
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {key}",
            "User-Agent": "dotfiles-catalogue/1.0",
        },
    )
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
    for item in data:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        pricing = item.get("pricing")
        if (
            isinstance(pricing, dict)
            and isinstance(pricing.get("prompt"), str)
            and isinstance(pricing.get("completion"), str)
        ):
            _LIVE_CATALOGUE_PRICES.setdefault(provider, {})[str(item["id"])] = (
                pricing["prompt"],
                pricing["completion"],
            )
    return sorted(
        str(item["id"]) for item in data if isinstance(item, dict) and item.get("id")
    )


def _safe_models(provider):
    try:
        return [
            dict(item) if isinstance(item, dict) else _model_name(item)
            for item in iter_engine_models(provider)
            if _model_name(item)
        ]
    except Exception:
        return []


def _modality_include(name, model_type):
    return (
        model_type == "audio_stt"
        or (model_type == "audio_tts" and not is_known_unqualified_tts(name))
        or model_type
        in {
            "embedding",
            "embeddings",
            "rerank",
            "reranking",
            "reranker",
            "conversion",
            "markitdown",
        }
    )


def _modality_endpoint_entries(provider, base_url, key_env, environ):
    key_env = key_env if key_env and environ.get(key_env, "").strip() else None
    entries = []
    for item in _safe_models(provider):
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        model_type = item.get("model_type")
        if provider == "ollama" and model_type is None and name:
            # R8 P0.1 typed publication: production Ollama discovery is
            # untyped (name/size/provider only). Classify candidates at this
            # boundary through the bounded, cached /api/show hook so an
            # installed-but-untyped embedding model can reach the gateway;
            # failures and non-embedding results stay UNKNOWN honestly.
            metadata = _ollama_metadata(name)
            capabilities = metadata.get("capabilities") or []
            if "embedding" in capabilities:
                model_type = "embedding"
        if name and _modality_include(name, model_type):
            entries.extend(
                [
                    _entry(
                        f"{provider}/{name}",
                        f"openai/{name}",
                        api_base=base_url,
                        key_env=key_env,
                        # Keyless local engines (api_key_env None) still need a
                        # non-empty api_key on the openai/ transport: LiteLLM
                        # refuses wire calls with HTTP 500 missing credentials
                        # otherwise. `none` is the doctor-legal no-credential
                        # placeholder (verify-config.py allows none or
                        # os.environ/<name>; the local upstream — Ollama — is
                        # unauthenticated by design and ignores it).
                        key_literal=None if key_env else "none",
                    ),
                ]
            )
    return entries


def _modality_entries(environ):
    """Build exact-id aliases for discovered local non-chat models."""
    entries = []
    for provider in sorted(active_engines()):
        endpoint = local_endpoint_for(provider, "openai")
        if endpoint is not None:
            entries.extend(_modality_endpoint_entries(provider, *endpoint, environ))
    names = {}
    for item in entries:
        names.setdefault(item["model_name"].split("/", 1)[1], []).append(item)
    for model_id, qualified in sorted(names.items()):
        if len(qualified) == 1:
            item = qualified[0]
            provider = item["model_name"].split("/", 1)[0]
            api_key_value = item["litellm_params"].get("api_key", "")
            key_env = None
            key_literal = None
            if api_key_value.startswith("os.environ/"):
                key_env = api_key_value[len("os.environ/") :] or None
            elif api_key_value:
                key_literal = api_key_value
            entries.append(
                _entry(
                    model_id,
                    item["litellm_params"]["model"],
                    api_base=item["litellm_params"].get("api_base"),
                    key_env=key_env,
                    key_literal=key_literal,
                    alias_kind="bare",
                )
            )
    return entries


def _registry_model_refs():
    """Collect canonical references from managed model registries and tier docs."""
    root = Path(__file__).resolve().parents[2]
    refs = set()
    for path in (
        root / "configs/junie/model-groups.json",
        root / "configs/opencode/oh-my-opencode-slim.json",
    ):
        try:
            refs.update(
                _collect_model_refs(json.loads(path.read_text(encoding="utf-8")))
            )
        except (OSError, json.JSONDecodeError):
            continue
    try:
        refs.update(
            _markdown_model_refs((root / "docs/TIERS.md").read_text(encoding="utf-8"))
        )
    except OSError:
        pass
    return refs


def _collect_model_refs(source):
    """Collect model-field references while respecting each field's provider."""
    refs = set()
    prefixes = (
        "ollama-cloud/",
        "opencode/",
        "openrouter/",
        "google/models/",
        "openai/",
        "cohere/",
        "poolside/",
    )

    def collect_fallback(value):
        if isinstance(value, str):
            if value.startswith(prefixes):
                refs.add(value)
        elif isinstance(value, (dict, list)):
            iterable = value.values() if isinstance(value, dict) else value
            for child in iterable:
                collect_fallback(child)

    def collect(value):
        if isinstance(value, dict):
            provider = value.get("provider")
            for field, provider_key in (
                ("model", None),
                ("primaryModel", "provider"),
                ("fasterModel", "fasterProvider"),
            ):
                model = value.get(field)
                model_provider = (
                    (value.get(provider_key) or provider) if provider_key else provider
                )
                if not isinstance(model, str) or model.startswith(
                    ("http://", "https://", "_local:")
                ):
                    continue
                if model_provider in (
                    "openrouter",
                    "google",
                    "openai",
                    "ollama-cloud",
                    "opencode",
                    "poolside",
                    "cohere",
                ):
                    expected_prefix = {
                        "openrouter": "openrouter/",
                        "google": "google/models/",
                        "openai": "openai/",
                        "ollama-cloud": "ollama-cloud/",
                        "opencode": "opencode/",
                        "poolside": "poolside/",
                        "cohere": "cohere/",
                    }[model_provider]
                    if model.startswith(expected_prefix):
                        refs.add(model)
                    elif model_provider == "google":
                        refs.add(f"google/models/{model.removeprefix('models/')}")
                    else:
                        refs.add(f"{model_provider}/{model}")
                elif model.startswith(prefixes):
                    refs.add(model)
            for key, child in value.items():
                if key == "fallback":
                    collect_fallback(child)
                elif key not in ("model", "primaryModel", "fasterModel"):
                    collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(source)
    return refs


def _markdown_model_refs(document):
    """Return qualified model references from tier-table model cells."""
    import re

    refs = set()
    in_tier = False
    for line in document.splitlines():
        if line.startswith("### "):
            in_tier = " Tier" in line
        if not in_tier or "|" not in line:
            continue
        for cell in line.split("|"):
            refs.update(
                match.group(1)
                for match in re.finditer(
                    r"`((?:ollama-cloud|opencode|openrouter|google/models|openai)/[A-Za-z0-9._:/-]+)`",
                    cell,
                )
            )
    return refs


def _provider_alias_prefix(provider):
    return "cohere_chat/" if provider == "cohere" else provider + "/"


# Providers whose ledger families are preserved across live-catalogue
# outages, with the alias namespaces each provider owns. The retention
# helper enforces binding/credential parity ONLY for these providers;
# every other live-catalogue provider keeps the historical unconditional
# prefix retention.
RETENTION_NAMESPACES = {
    "cohere": ("cohere_chat/", "cohere/"),
    "poolside": ("poolside/",),
}


def _retained_ledger_families(
    provider, previous_entries, *, key_env, expected_api_base
):
    """Previously verified entries for a provider's alias namespaces.

    Deterministic prior-alias retention (per the #79 review finding): on an
    unavailable catalogue, preserve already-served records for both Cohere
    namespaces and Poolside only while the previous upstream binding and
    credential policy remain valid — the stored entry must still reference
    the same ``os.environ/<key_env>``-shaped credential and the same
    ``api_base`` (``None`` for native providers). Never fabricates new
    aliases on UNKNOWN; revoked or rebinded credentials are not preserved.
    Retained aliases are preserved but runtime UNKNOWN (configuration identity
    only; this does not prove the stored secret is still valid).
    Providers outside RETENTION_NAMESPACES keep the historical
    unconditional prefix retention (``key_env``/``expected_api_base``
    ignored).
    """
    namespaces = RETENTION_NAMESPACES.get(provider, (f"{provider}/",))
    strict = provider in RETENTION_NAMESPACES
    retained = []
    for item in previous_entries or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("model_name", ""))
        if not name.startswith(namespaces):
            continue
        params = item.get("litellm_params", {})
        if strict:
            if params.get("api_key") != f"os.environ/{key_env}":
                continue
            if params.get("api_base") != expected_api_base:
                continue
        retained.append(
            {
                **item,
                "_alias_kind": "qualified",
                "_upstream_ref": params.get("model", ""),
            }
        )
    return retained


def compute_model_list(environ=None, previous_entries=None):
    """Return LiteLLM model entries without reading secrets or doing writes."""
    environ = environ or os.environ
    global last_generation_notes
    last_generation_notes = []
    # Per-invocation sidecar: rates captured during this fetch cycle only.
    _LIVE_CATALOGUE_PRICES.clear()
    entries = []
    previous_entries = previous_entries or []
    previous_by_alias = {
        item.get("model_name"): item
        for item in previous_entries
        if isinstance(item, dict) and isinstance(item.get("model_name"), str)
    }
    # Keep modality discovery separate: these rows must never enter chat candidates.
    modality_entries = _modality_entries(environ)
    modality_names = {
        item["model_name"] for item in modality_entries if item["_alias_kind"] == "bare"
    }
    # Catalogue scope gate: free (default) emits only curated selections plus
    # OpenRouter's genuinely-free tier; full restores the whole enumerated
    # catalogue. Unknown values are a hard configuration error.
    scope = str(environ.get("DOTFILES_LITELLM_CATALOGUE_SCOPE", "free")).strip().lower()
    if scope not in {"free", "full"}:
        raise RuntimeError(
            f"unsupported DOTFILES_LITELLM_CATALOGUE_SCOPE {scope!r}: expected 'free' or 'full'"
        )
    for provider in active_engines():
        models = [
            _model_name(model)
            for model in _safe_models(provider)
            if not isinstance(model, dict)
            or model.get("model_type") in (None, "llm", "vlm")
        ]
        # Known specialized models are never published as chat candidates.
        # Typed audio/conversion records are already dropped above; this
        # name-level guard covers the untyped production Ollama discovery
        # shape (name/size/provider only — no model_type to filter on).
        # Gateway modality exposure is a separately approved milestone.
        models = [
            name for name in models if name and not is_specialized_model_name(name)
        ]
        models = sorted(models)
        # Local-family shrink guard (R6 recovery discipline): a gate-ON
        # engine's discovery outage is UNKNOWN evidence, never a removal
        # decision — the ledger's served family entries are retained and the
        # gap is recorded honestly. Bare twins regenerate on the next healthy
        # pass, so a degraded host keeps its namespaced family only.
        if not models:
            retained = [
                item
                for item in previous_entries
                if isinstance(item, dict)
                and str(item.get("model_name", "")).startswith(provider + "/")
            ]
            if retained:
                last_generation_notes.append(
                    f"{provider}: UNKNOWN local discovery unavailable; "
                    "ledger family retained (bare twins regenerate on recovery)"
                )
                entries.extend(
                    {**item, "_alias_kind": item.get("_alias_kind", "qualified")}
                    for item in retained
                )
            if provider != "ollama":
                # Other engines publish nothing else from local discovery.
                continue
            # ollama falls through: daemon cloud stubs publish separately.
        if provider == "ollama":
            local_base = get_ollama_local_base_url().rstrip("/")
            base = local_base[:-3] if local_base.endswith("/v1") else local_base
            cloud_models = [_model_name(item) for item in list_cloud_ollama_models()]
            # The regular engine pool excludes cloud stubs, but tolerate mixed
            # pools and preserve one pair of aliases per installed model.
            ollama_models = sorted(dict.fromkeys(models + cloud_models))
            for model in ollama_models:
                # Daemon cloud stubs bill against the Ollama plan — price
                # them; bare local names (user hardware) stay unpriced. Stubs
                # emit ONLY the ollama/-namespaced alias: the naked
                # '<id>:cloud' twin is a duplicate of the same daemon route.
                is_stub = model.endswith(":cloud") or model.endswith("-cloud")
                cloud_info = (
                    model_rates("ollama", model, f"ollama/{model}") if is_stub else None
                )
                entries.append(
                    _entry(
                        f"ollama/{model}",
                        f"ollama/{model}",
                        api_base=base,
                        model_info=cloud_info,
                    )
                )
                if not is_stub:
                    entries.append(
                        _entry(
                            model,
                            f"ollama/{model}",
                            api_base=base,
                            alias_kind="bare",
                        )
                    )
            continue
        endpoint = local_endpoint_for(provider, "openai")
        if endpoint is None:
            # The engine's protocol endpoint is unresolvable: UNKNOWN
            # evidence — retain the family rather than silently dropping it.
            retained = [
                item
                for item in previous_entries
                if isinstance(item, dict)
                and str(item.get("model_name", "")).startswith(provider + "/")
            ]
            if retained:
                last_generation_notes.append(
                    f"{provider}: UNKNOWN local endpoint unavailable; ledger family retained"
                )
                entries.extend(
                    {**item, "_alias_kind": item.get("_alias_kind", "qualified")}
                    for item in retained
                )
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
                        alias_kind="bare",
                    ),
                ]
            )

    if is_meridian_configured() and environ.get("MERIDIAN_API_KEY", "").strip():
        allowlist = (
            Path(__file__).resolve().parents[2]
            / "configs/opencode/anthropic-models.json"
        )
        model_ids = sorted(json.loads(allowlist.read_text(encoding="utf-8"))["models"])
        # Meridian's user-facing surface includes /v1, but the Anthropic wire
        # adapter appends /v1/messages itself (same shape as the direct
        # api.anthropic.com entry), so the wire base must be root-only.
        meridian_wire_base = get_meridian_base_url().rstrip("/")
        if meridian_wire_base.endswith("/v1"):
            meridian_wire_base = meridian_wire_base[: -len("/v1")]
        entries.extend(
            _entry(
                f"meridian/{model_id}",
                f"anthropic/{model_id}",
                api_base=meridian_wire_base,
                key_env="MERIDIAN_API_KEY",
                # Claude wires are priced here explicitly: models.dev rates
                # first, then the installed cost map — the bundled map only
                # aliases bare claude ids, so the namespaced lookup LiteLLM
                # runs at request time misses and logs $0 spend.
                model_info=model_rates("meridian", model_id, f"anthropic/{model_id}"),
            )
            for model_id in model_ids
        )

    # refs are loop-invariant (two JSON reads + a TIERS.md regex pass per
    # call) — compute once instead of per provider iteration.
    refs = _registry_model_refs()
    clouds = {
        "openai": (
            "OPENAI_API_KEY",
            "openai/gpt-6-luna",
            "https://api.openai.com/v1",
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
        "cerebras": (
            "CEREBRAS_API_KEY",
            "cerebras/llama-3.3-70b",
            PROVIDER_ENDPOINTS["cerebras"]["baseUrl"],
        ),
        "cohere": ("COHERE_API_KEY", "", PROVIDER_ENDPOINTS["cohere"]["baseUrl"]),
        "huggingface": ("HF_TOKEN", "", PROVIDER_ENDPOINTS["huggingface"]["baseUrl"]),
    }
    for provider, (key_env, model, base_url) in clouds.items():
        requested = {
            ref.split("/", 1)[1] for ref in refs if ref.startswith(provider + "/")
        }
        if provider == "google":
            requested = {model_id.removeprefix("models/") for model_id in requested}
        key = environ.get(key_env, "").strip()
        if provider == "openai" and not key:
            if (
                constants.get_litellm_oauth_gate(environ)
                and oauth_cache_expiry("chatgpt", environ) is not None
            ):
                verified = chatgpt_verified_openai_models(environ)
                for model_id in sorted(requested & verified):
                    entries.append(
                        _entry(
                            f"openai/{model_id}",
                            f"chatgpt/{model_id}",
                            # Subscription lanes price at the provider's API
                            # rates as an estimate so usage is comparable.
                            model_info=model_rates(
                                "openai", model_id, f"chatgpt/{model_id}"
                            ),
                        )
                    )
                for model_id in sorted(requested - verified):
                    last_generation_notes.append(
                        f"openai/{model_id}: subscription-transport candidate NOT verified — run litellm-oauth.py --provider chatgpt which verifies ids"
                    )
                if requested and not requested & verified:
                    last_generation_notes.append(
                        "openai: UNKNOWN no verified ChatGPT subscription models"
                    )
                continue
            last_generation_notes.extend(
                f"openai/{model_id}: UNKNOWN OPENAI_API_KEY is unset"
                for model_id in sorted(requested)
            )
            continue
        if not key:
            continue
        if provider == "opencode":
            confirmed = set(provider_models("opencode"))
        elif provider in LIVE_CATALOGUE_PROVIDERS and provider != "poolside":
            live_base = PROVIDER_ENDPOINTS.get(provider, {}).get("baseUrl", base_url)
            try:
                confirmed = set(_live_catalogue(provider, key, live_base))
            except LiveCatalogueError:
                last_generation_notes.append(
                    f"{provider}: UNKNOWN; ledger family retained"
                )
                # Unknown live evidence cannot revoke a model already served;
                # previously verified families persist only while their
                # upstream binding and credential policy remain valid.
                if provider in RETENTION_NAMESPACES:
                    expected_base = (
                        PROVIDER_ENDPOINTS.get(provider, {}).get("baseUrl", base_url)
                        if provider == "poolside"
                        else None
                    )
                    retained = _retained_ledger_families(
                        provider,
                        previous_entries,
                        key_env=key_env,
                        expected_api_base=expected_base,
                    )
                else:
                    retained = _retained_ledger_families(
                        provider,
                        previous_entries,
                        key_env=None,
                        expected_api_base=None,
                    )
                entries.extend(retained)
                continue
        else:
            confirmed = set()
        # Canonicalize confirmed ids to the bare gateway-alias spelling so the
        # comparison matches how requested ids are collected (strips models/
        # and :free transport spellings).
        # Keep the raw catalogue ids: the free scope needs the upstream
        # ':free' spelling, which canonicalization intentionally removes.
        raw_confirmed = set(confirmed)
        confirmed = {canonical_allowlist_key(item) for item in confirmed}
        # Compare in the bare gateway-alias space on BOTH sides: requested
        # carries upstream spellings like ':free' or 'models/' that the
        # canonicalized confirmed set never contains.
        canonical_requested = {
            canonical_allowlist_key(model_id) for model_id in requested
        }
        for model_id in sorted(canonical_requested - confirmed):
            last_generation_notes.append(
                f"{provider}/{model_id}: UNKNOWN registry reference is not in catalogue/allowlist"
            )
        if scope == "free":
            # Free scope: emit only curated selections (allowlist/TIERS
            # references). OpenRouter and the cloud-capable local daemon
            # additionally keep upstream ids whose raw catalogue spelling
            # identifies them (':free' suffix / ':cloud' stubs).
            curated = confirmed & canonical_requested
            if provider in ("openrouter", "ollama-cloud"):
                # Curated keys are bare canonical ids; map them back to the
                # matching raw upstream spelling so only ONE alias per model
                # is emitted. Upstream may carry bare and suffixed spellings
                # of the same model under one canonical key — prefer the
                # longer (transport-suffixed) spelling deterministically so
                # ':free' models never also emit a bare paid-route twin.
                raw_upstream_by_key = {}
                for item in sorted(raw_confirmed):
                    key = canonical_allowlist_key(item)
                    held = raw_upstream_by_key.get(key)
                    if held is None or len(item) > len(held):
                        raw_upstream_by_key[key] = item
                curated = {raw_upstream_by_key.get(item, item) for item in curated}
                if provider == "openrouter":
                    curated |= {
                        item for item in raw_confirmed if item.endswith(":free")
                    }
                confirmed_models = curated
            else:
                confirmed_models = curated
        else:
            confirmed_models = confirmed
        if provider == "poolside":
            # Single-path policy: poolside fresh emission belongs to the
            # standalone block below (the main loop's poolside branch was
            # removed so cross-namespace catalogue ids can never leak into
            # poolside aliases). The loop above still applies outage
            # retention for poolside, and the LIVE_CATALOGUE_PROVIDERS
            # requested-absent retention below keeps registry-requested
            # families stable across catalogue changes.
            confirmed_models = set()
        if not confirmed_models and provider != "poolside":
            last_generation_notes.append(f"{provider}: UNKNOWN (no confirmed models)")
            continue
        if provider in LIVE_CATALOGUE_PROVIDERS:
            retained = []
            # Requested-absent retention honours every alias namespace the
            # provider owns (cohere keeps BOTH the cohere_chat/<id> wire and
            # the canonically-spelled cohere/<id> twin; other providers keep
            # their single spelling) so a temporarily delisted selection
            # cannot strand a saved Slim preset on one spelling only.
            namespaces = RETENTION_NAMESPACES.get(
                provider, (_provider_alias_prefix(provider),)
            )
            for alias, previous in previous_by_alias.items():
                if not isinstance(alias, str) or not alias.startswith(namespaces):
                    continue
                for ns in namespaces:
                    if alias.startswith(ns):
                        model_id = alias[len(ns) :]
                        break
                else:  # pragma: no cover - startswith(namespaces) guarantees a hit
                    continue
                if (
                    canonical_allowlist_key(model_id) in canonical_requested
                    and canonical_allowlist_key(model_id) not in confirmed
                ):
                    retained.append(
                        {
                            **previous,
                            "_alias_kind": "qualified",
                            "_upstream_ref": previous.get("litellm_params", {}).get(
                                "model", ""
                            ),
                        }
                    )
            entries.extend(retained)
        for model_id in sorted(confirmed_models):
            if provider == "cohere":
                alias = wire_model = f"cohere_chat/{model_id}"
            elif provider == "huggingface":
                alias = wire_model = f"huggingface/{model_id}"
            elif provider == "cerebras":
                alias = wire_model = f"cerebras/{model_id}"
            elif provider == "google":
                alias = f"google/models/{model_id.removeprefix('models/')}"
                wire_model = f"gemini/{model_id.removeprefix('models/')}"
            else:
                alias = f"{provider}/{model_id}"
                wire_model = f"openai/{model_id}"
            model_info = None
            if provider == "openrouter":
                model_info = openrouter_pricing_for(model_id)
            if model_info is None:
                # Live-sidecar pricing wins; otherwise models.dev rates and
                # finally the installed LiteLLM cost map. Free and
                # subscription rows stay honest ($0 by live rates;
                # unpriced by design respectively).
                model_info = model_rates(provider, model_id, wire_model)
            entries.append(
                _entry(
                    alias,
                    wire_model,
                    api_base=(
                        None
                        if provider in ("cohere", "huggingface", "cerebras", "google")
                        else PROVIDER_ENDPOINTS.get(provider, {}).get(
                            "baseUrl", base_url
                        )
                    ),
                    key_env=key_env,
                    model_info=model_info,
                )
            )
            if provider == "cohere":
                # Canonically-spelled companion alias (mirrors the
                # anthropic/ meridian twin): slim selections and audits
                # reference cohere/<id> while the wire is cohere_chat/<id>.
                entries.append(
                    _entry(
                        f"cohere/{model_id}",
                        f"cohere_chat/{model_id}",
                        key_env=key_env,
                        model_info=model_info,
                    )
                )
            # Only confirmed direct API-key OpenAI routes may publish service
            # lanes; OAuth entries use chatgpt/ and never reach this branch.
            if (
                provider == "openai"
                and key_env == "OPENAI_API_KEY"
                and base_url
                == PROVIDER_ENDPOINTS.get("openai", {}).get(
                    "baseUrl", "https://api.openai.com/v1"
                )
                and environ.get("OPENAI_API_KEY", "").strip()
                and environ.get(OPENAI_SERVICE_LANES_ENV, "0") == "1"
                and model_id in OPENAI_SERVICE_LANE_MODELS
            ):
                base_entry = entries[-1]
                standard = model_info or model_rates("openai", model_id, wire_model)
                for lane in ("flex", "fast"):
                    lane_info = dict(standard or {})
                    multiplier = 0.5 if lane == "flex" else 2.0
                    for rate_key in (
                        "input_cost_per_token",
                        "output_cost_per_token",
                        "cache_read_input_token_cost",
                        "cache_creation_input_token_cost",
                    ):
                        value = lane_info.get(rate_key)
                        if isinstance(value, (int, float)):
                            lane_info[rate_key] = value * multiplier
                    lane_alias = f"{alias}-{lane}"
                    if f"{model_id}-{lane}" in confirmed_models or any(
                        item["model_name"] == lane_alias for item in entries
                    ):
                        raise ValueError(
                            f"OpenAI service-lane alias collision: {lane_alias}"
                        )
                    entries.append(
                        _entry(
                            lane_alias,
                            wire_model,
                            api_base=base_entry["litellm_params"].get("api_base"),
                            key_env="OPENAI_API_KEY",
                            model_info=lane_info,
                            service_tier=lane,
                        )
                    )
    if environ.get("POOLSIDE_API_KEY", "").strip():
        poolside_requested = {
            ref.split("/", 1)[1] for ref in refs if ref.startswith("poolside/")
        }
        try:
            poolside_models = _live_catalogue(
                "poolside",
                environ["POOLSIDE_API_KEY"].strip(),
                PROVIDER_ENDPOINTS["poolside"]["baseUrl"],
            )
        except LiveCatalogueError as error:
            # Same retention contract as the provider loop's outage handler:
            # previously verified poolside families persist while their
            # upstream binding and credential policy remain valid.
            last_generation_notes.append("poolside: UNKNOWN; ledger family retained")
            entries.extend(
                _retained_ledger_families(
                    "poolside",
                    previous_entries,
                    key_env="POOLSIDE_API_KEY",
                    expected_api_base=PROVIDER_ENDPOINTS["poolside"]["baseUrl"],
                )
            )
        else:
            confirmed = {
                model_id.removeprefix("poolside/")
                for model_id in poolside_models
                if model_id.startswith("poolside/")
            }
            selected = confirmed & poolside_requested
            if scope == "full":
                selected = confirmed
            # Requested-absent retention mirrors the provider-loop contract:
            # a temporarily delisted but still-registry-requested poolside
            # selection stays served (stable alias mapping across catalogue
            # changes and restarts).
            for alias, previous in previous_by_alias.items():
                if not isinstance(alias, str) or not alias.startswith("poolside/"):
                    continue
                model_id = alias[len("poolside/") :]
                if (
                    canonical_allowlist_key(model_id)
                    in {canonical_allowlist_key(ref) for ref in poolside_requested}
                    and canonical_allowlist_key(model_id) not in confirmed
                ):
                    entries.append(
                        {
                            **previous,
                            "_alias_kind": "qualified",
                            "_upstream_ref": previous.get("litellm_params", {}).get(
                                "model", ""
                            ),
                        }
                    )
            for model_id in sorted(selected):
                entries.append(
                    _entry(
                        f"poolside/{model_id}",
                        # Poolside is not a native LiteLLM provider; the
                        # generic OpenAI-compatible client (openai/ prefix +
                        # api_base) is the only wire shape the router keeps.
                        # Poolside's API expects the model id WITH its
                        # provider prefix (catalogue ids are poolside/…),
                        # matching the OpenRouter-hosted free variants.
                        f"openai/poolside/{model_id}",
                        api_base=PROVIDER_ENDPOINTS["poolside"]["baseUrl"],
                        key_env="POOLSIDE_API_KEY",
                    )
                )

    # Anthropic coverage comes solely from the meridian block above; the
    # clouds loop has no anthropic entry because no ref collector emits
    # anthropic/-prefixed ids (slim anthropic selections translate to
    # meridian/<id>). Keep the signal honest: silent when meridian covers
    # it, distinctive when an Anthropic key exists without meridian.
    anthropic_key = environ.get("ANTHROPIC_API_KEY", "").strip()
    meridian_key = environ.get("MERIDIAN_API_KEY", "").strip()
    meridian_covered = is_meridian_configured() and bool(meridian_key)
    if anthropic_key and meridian_covered:
        last_generation_notes.append("anthropic: covered by meridian aliases")
    elif anthropic_key and not meridian_covered:
        last_generation_notes.append(
            "anthropic: UNKNOWN configured but no meridian gateway — anthropic selections have no gateway alias (set MERIDIAN_API_KEY)"
        )

    oauth_models = {
        "github_copilot": ("gpt-4o",),
        "chatgpt": ("gpt-5.2",),
    }
    for provider, model_ids in oauth_models.items():
        if not constants.get_litellm_oauth_gate(environ):
            last_generation_notes.append(
                f"OAuth provider {provider} withheld: DOTFILES_LITELLM_OAUTH_PROVIDERS=0 — set =1 only on machines where proxy device-flow pauses are acceptable"
            )
            continue
        if oauth_cache_expiry(provider, environ) is None:
            last_generation_notes.append(
                f"{provider}: OAuth token cache absent/expired — run litellm-oauth.py --provider {provider}"
            )
            continue
        entries.extend(
            (
                _entry(
                    f"github-copilot/{model_id}",
                    f"github_copilot/{model_id}",
                )
                if provider == "github_copilot"
                else _entry(f"{provider}/{model_id}", f"{provider}/{model_id}")
            )
            for model_id in model_ids
        )
    chat_names = {item["model_name"] for item in entries}
    collision = chat_names & modality_names
    if collision:
        raise ValueError(f"chat/modality model_name collision: {sorted(collision)}")
    entries.extend(modality_entries)
    try:
        routing_port = int(environ.get("LITELLM_PORT", "4000"))
    except (TypeError, ValueError):
        routing_port = 4000
    qualified = {
        item["model_name"] for item in entries if item["_alias_kind"] == "qualified"
    }
    bare_upstreams = {}
    for item in entries:
        if item["_alias_kind"] == "bare":
            bare_upstreams.setdefault(item["model_name"], set()).add(
                item["_upstream_ref"]
            )
    unambiguous_bare = {
        alias
        for alias, upstreams in bare_upstreams.items()
        if len(upstreams) == 1 and alias not in qualified
    }
    seen_qualified = set()
    seen_bare = set()
    filtered = []
    for item in entries:
        alias = item["model_name"]
        if item["_alias_kind"] == "qualified":
            if alias in seen_qualified:
                continue
            seen_qualified.add(alias)
        else:
            if alias not in unambiguous_bare or alias in seen_bare:
                continue
            seen_bare.add(alias)
        filtered.append(item)
    entries = sorted(filtered, key=lambda item: item["model_name"])
    validate_routing_entries(entries, port=routing_port)
    for item in entries:
        item.pop("_alias_kind", None)
        item.pop("_upstream_ref", None)
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


def previous_entries_ledger_path(config_path):
    """Companion JSON ledger alongside the deployed YAML config."""
    path = Path(config_path)
    return path.parent / (path.stem + ".models.json")


def read_previous_entries(config_path):
    """Read previously deployed model entries from the stdlib JSON ledger.

    The ledger is written beside the config on every successful generation;
    it must exist for retention. Reads stay stdlib-only: the deploy template's
    interpreter has no PyYAML.
    """
    ledger = previous_entries_ledger_path(config_path)
    if not ledger.is_file():
        return []
    try:
        entries = json.loads(ledger.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return entries if isinstance(entries, list) else []


def stage_backup_entries(backup_path, authorized_entries, environ=None):
    """Stage backup aliases only where current caller-supplied authorization exists."""
    backup = Path(backup_path)
    if not backup.is_file():
        raise FileNotFoundError(f"LiteLLM recovery backup is unavailable: {backup}")
    text = backup.read_text(encoding="utf-8")
    return stage_backup_text(text, authorized_entries, environ=environ)


def stage_backup_text(text, authorized_entries, environ=None):
    """Stage restricted backup YAML, using only independently authorized routes."""
    lines = text.splitlines()
    if not lines or lines[0] not in {"model_list:", "model_list: []"}:
        raise ValueError("unsupported LiteLLM recovery document header")
    index = 1
    while index < len(lines) and lines[index].startswith("  - model_name: "):
        index += 1
        if index >= len(lines) or lines[index] != "    litellm_params:":
            raise ValueError("truncated LiteLLM recovery entry")
        index += 1
        while index < len(lines) and lines[index].startswith("      "):
            index += 1
    if index >= len(lines) or lines[index] != "litellm_settings:":
        raise ValueError("truncated or malformed LiteLLM recovery model list")
    expected_tail = [
        "litellm_settings:",
        "  drop_params: true",
        "  telemetry: false",
        "general_settings:",
        "  master_key: os.environ/LITELLM_MASTER_KEY",
        "  database_url: os.environ/DATABASE_URL",
    ]
    if lines[index : index + len(expected_tail)] != expected_tail or any(
        line.strip() for line in lines[index + len(expected_tail) :]
    ):
        raise ValueError("unsupported or trailing LiteLLM recovery document content")
    entries = []
    matches = re.findall(
        r'^  - model_name: ("(?:[^"\\]|\\.)*")\n    litellm_params:\n((?:      [^\n]+\n)+)',
        text,
        re.MULTILINE,
    )
    if not matches and text.strip() != "model_list: []":
        raise ValueError("unsupported or empty LiteLLM recovery backup")
    if len(matches) != sum(line.startswith("  - model_name: ") for line in lines):
        raise ValueError("incomplete LiteLLM recovery entry")
    for model_name, params in matches:
        parsed = {}
        for line in params.splitlines():
            key, separator, value = line.strip().partition(": ")
            if not separator:
                raise ValueError("unsupported LiteLLM backup parameter syntax")
            parsed[key] = value if key == "api_key" else json.loads(value)
        entries.append({"model_name": json.loads(model_name), "litellm_params": parsed})
    backup_aliases = {entry["model_name"]: entry for entry in entries}
    authorized = {
        entry["model_name"]: entry
        for entry in authorized_entries
        if isinstance(entry, dict) and isinstance(entry.get("model_name"), str)
    }
    candidate = [authorized[alias] for alias in backup_aliases if alias in authorized]
    if not candidate:
        raise ValueError("backup contains no currently authorized model aliases")
    missing = sorted(set(authorized) - set(backup_aliases))
    if missing:
        logger.warning(
            "Authorized routes absent from recovery backup: %s", ", ".join(missing)
        )
    return render_config(environ=environ, entries=candidate), candidate


def write_previous_entries(config_path, entries):
    """Persist the deployed model entries to the companion JSON ledger."""
    ledger = previous_entries_ledger_path(config_path)
    if not ledger.parent.exists():
        ledger.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=ledger.parent, prefix=".models.", text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(list(entries or []), handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temp_name, ledger)
    return True
