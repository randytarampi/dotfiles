"""Pure LiteLLM proxy configuration generation from dotfiles registries."""

from __future__ import annotations

import json
import math
import logging
import os
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
from local_engines import active_engines, iter_engine_models, local_endpoint_for
from provider_endpoints import PROVIDER_ENDPOINTS, provider_models
from litellm_routing import routing_entries_are_safe
from litellm_aliases import canonical_allowlist_key  # noqa: E402 -- lib sibling import.
from model_catalogues import open_same_origin

logger = logging.getLogger(__name__)

# Cloud providers whose live /v1/models catalogue the generator enumerates
# into the model list. Non-enumerable providers (openai keyless, opencode
# 403) keep their single curated default alias instead.
LIVE_CATALOGUE_PROVIDERS = (
    "google",
    "openrouter",
    "ollama-cloud",
    "cerebras",
    "cohere",
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
        ("max_input_tokens", "max_input_tokens"),
        ("max_output_tokens", "max_output_tokens"),
        ("mode", "mode"),
    ):
        value = entry.get(source_key)
        if isinstance(value, (int, float, str)) and value is not None:
            info[info_key] = value
    return info


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
    alias, model, *, api_base=None, key_env=None, alias_kind=None, model_info=None
):
    params = {"model": model}
    if api_base:
        params["api_base"] = api_base
    if key_env:
        params["api_key"] = f"os.environ/{key_env}"
    if model_info:
        params["model_info"] = model_info
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
            _model_name(item)
            for item in iter_engine_models(provider)
            if _model_name(item)
        ]
    except Exception:
        return []


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
                ):
                    expected_prefix = {
                        "openrouter": "openrouter/",
                        "google": "google/models/",
                        "openai": "openai/",
                        "ollama-cloud": "ollama-cloud/",
                        "opencode": "opencode/",
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


def compute_model_list(environ=None):
    """Return LiteLLM model entries without reading secrets or doing writes."""
    environ = environ or os.environ
    global last_generation_notes
    last_generation_notes = []
    # Per-invocation sidecar: rates captured during this fetch cycle only.
    _LIVE_CATALOGUE_PRICES.clear()
    entries = []
    # Catalogue scope gate: free (default) emits only curated selections plus
    # OpenRouter's genuinely-free tier; full restores the whole enumerated
    # catalogue. Unknown values are a hard configuration error.
    scope = str(environ.get("DOTFILES_LITELLM_CATALOGUE_SCOPE", "free")).strip().lower()
    if scope not in {"free", "full"}:
        raise RuntimeError(
            f"unsupported DOTFILES_LITELLM_CATALOGUE_SCOPE {scope!r}: expected 'free' or 'full'"
        )
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
                bare = _entry(
                    model, f"ollama/{model}", api_base=base, alias_kind="bare"
                )
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
                # Claude wires are priced here explicitly: the bundled cost
                # map only aliases bare claude ids, so the namespaced lookup
                # LiteLLM runs at request time misses and logs $0 spend.
                model_info=builtin_rates(f"anthropic/{model_id}"),
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
                    entries.append(_entry(f"openai/{model_id}", f"chatgpt/{model_id}"))
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
        elif provider in LIVE_CATALOGUE_PROVIDERS:
            live_base = PROVIDER_ENDPOINTS.get(provider, {}).get("baseUrl", base_url)
            try:
                confirmed = set(_live_catalogue(provider, key, live_base))
            except LiveCatalogueError as error:
                last_generation_notes.append(f"{provider}: UNKNOWN ({error})")
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
                # is emitted (no bare paid-route twin for OpenRouter).
                raw_upstream_by_key = {
                    canonical_allowlist_key(item): item for item in raw_confirmed
                }
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
        if not confirmed_models:
            last_generation_notes.append(f"{provider}: UNKNOWN (no confirmed models)")
            continue
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
                # Live-sidecar pricing wins; otherwise price from the
                # installed LiteLLM cost map. Free and subscription rows stay
                # honest ($0 by live rates; unpriced by design respectively).
                model_info = builtin_rates(wire_model)
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
