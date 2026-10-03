#!/usr/bin/env python3
"""Check model assignments against checked-in and live model catalogs."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
import stat

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))

import logger  # noqa: E402  # sys.path is bootstrapped for shared repo libraries.
from env import load_env
from model_stamp import is_stale
from local_engines import active_engines, iter_engine_models_strict, resolve_engine
from model_catalogues import (  # noqa: E402  # sys.path bootstrap above is intentional.
    endpoint_models_url,
    get_catalogue,
    get_models as fetch_models,
    load_allowlists,
    _configured_litellm_port,
)
from model_references import (  # noqa: E402  # sys.path bootstrap above is intentional.
    Catalogue,
    EndpointIdentity,
    ModelReference,
    Outcome,
    compare_references,
    google_direct_id,
)

REPO_ROOT = SCRIPT_DIR.parent
DRIFT_STATS = {"checked": 0, "skipped": 0}
SLIM_PATH = REPO_ROOT / "configs" / "opencode" / "oh-my-opencode-slim.json"
# Google/OpenRouter entries are checked for internal allowlist membership only;
# they are not queried against live catalogs. Refresh via free-preset skill.
ALLOWLISTS = {
    provider: REPO_ROOT / "configs" / "opencode" / f"{provider}-models.json"
    for provider in (
        "openai",
        "anthropic",
        "ollama-cloud",
        "github-copilot",
        "opencode",
        "google",
        "openrouter",
    )
}


def get_models(url: str, api_key: str = ""):
    return fetch_models(url, api_key, stats=DRIFT_STATS)


def iter_models(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "model" and isinstance(child, str):
                yield child
            else:
                yield from iter_models(child)
    elif isinstance(value, list):
        for child in value:
            if isinstance(child, str):
                yield child
            else:
                yield from iter_models(child)


def resolve_profile_api_key(value: object) -> tuple[str | None, str]:
    """Resolve only literal and ${ENV} key forms; never evaluate shell syntax."""
    if not isinstance(value, str):
        return "", "anonymous"
    match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", value)
    if match:
        name = match.group(1)
        return os.environ.get(name), f"env:{name}"
    if value.startswith("!") or "$(`" in value or "$(" in value:
        return None, "unsupported-profile-expression"
    return (value, "inline-profile-key") if value else ("", "anonymous")


def check_slim(data: dict) -> list[str]:
    violations = []
    allowlists = load_allowlists(ALLOWLISTS)
    for model in set(iter_models(data)):
        if model.startswith("_local:") or any(
            model.startswith(f"{provider}/") for provider in active_engines()
        ):
            continue
        if "/" not in model:
            continue
        provider, model_id = model.split("/", 1)
        if provider not in allowlists:
            violations.append(f"{model} uses an unavailable {provider} model allowlist")
        elif model_id not in allowlists[provider]:
            violations.append(f"{model} is not in the {provider} model allowlist")
    return violations


def deployed_engine_references(provider: str) -> set[str]:
    """Collect concrete model IDs for one registered engine."""
    paths = [
        Path("~/.config/opencode/oh-my-opencode-slim.json").expanduser(),
        Path("~/.pi/agent/models.json").expanduser(),
        Path("~/.junie-local/model-groups.json").expanduser(),
    ]
    paths.extend(Path("~/.junie/models").expanduser().glob("*.json"))
    references = set()
    for path in paths:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
            document = json.loads(text)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(document, dict):
            continue
        providers = document.get("providers", {})
        if isinstance(providers, dict) and isinstance(providers.get(provider), dict):
            for model in providers[provider].get("models", []):
                if isinstance(model, dict) and model.get("id"):
                    references.add(model["id"])
        groups = document.get("groups", {})
        if isinstance(groups, dict):
            for group in groups.values():
                if isinstance(group, dict) and group.get("provider") == provider:
                    for key in ("primaryModel", "fasterModel"):
                        if isinstance(group.get(key), str):
                            references.add(group[key].split("/", 1)[-1])
        for model in document.get("models", []):
            if isinstance(model, dict) and model.get("provider") == provider:
                references.add(model.get("id", model.get("name", "")))
    return references


def check_local_engine_models() -> list[str]:
    """Check deployed references against each active engine's live catalogue."""
    violations = []
    for provider in active_engines():
        engine = resolve_engine(provider)
        if not engine or not engine.get("drift_check"):
            continue
        references = deployed_engine_references(provider)
        if not references:
            continue
        try:
            engine_models = iter_engine_models_strict(provider)
            if engine_models is None:
                logger.warning(
                    "Could not reach %s engine — skipping live model drift", provider
                )
                continue
            live_models = {model["name"] for model in engine_models}
        except Exception as exc:
            logger.warning(
                "Could not reach %s engine — skipping live model drift (%s)",
                provider,
                exc,
            )
            continue
        violations.extend(
            f"{provider} model {model} is not present in the live catalogue"
            for model in sorted(references - live_models)
        )
    return violations


def _safe_endpoint_namespace(base_url: str) -> str:
    parsed = urlsplit(endpoint_models_url(base_url))
    try:
        host = parsed.hostname or "unknown-host"
        if parsed.port:
            host += f":{parsed.port}"
    except ValueError:
        host = "invalid-host"
    return urlunsplit((parsed.scheme, host, parsed.path.rstrip("/"), "", ""))


def _profile_reference(path, provider_name, provider, model, field):
    if not isinstance(model, str) or not model:
        return None
    provider = provider if isinstance(provider, dict) else {}
    base_url = provider.get("baseUrl")
    if not isinstance(base_url, str):
        base_url = ""
    raw_key = provider.get("apiKey", "")
    if raw_key == "" and provider.get("apiKeyEnv"):
        raw_key = "${" + str(provider["apiKeyEnv"]) + "}"
    key, scope = resolve_profile_api_key(raw_key)
    if not base_url:
        key = None
        scope = f"unresolved-provider:{provider_name}"
    if scope == "inline-profile-key":
        scope = f"inline-profile:{Path(path).resolve()}:{field}:{provider_name}"
    namespace = (
        _safe_endpoint_namespace(base_url)
        if base_url
        else f"unresolved-provider:{provider_name}"
    )
    endpoint = EndpointIdentity(namespace, scope)
    return {
        "endpoint": endpoint,
        "base_url": base_url,
        "api_key": key,
        "reference": ModelReference(endpoint, model, str(path), field),
    }


def profile_models(path: Path) -> list[dict] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            logger.warning(
                "Could not read Junie profile %s — skipping (JSON root is not an object)",
                path,
            )
            return None
        if isinstance(data.get("providers"), dict) and isinstance(
            data.get("groups"), dict
        ):
            entries = []
            for group in data["groups"].values():
                if not isinstance(group, dict):
                    continue
                provider_name = group.get("provider", "")
                provider = data["providers"].get(provider_name, {})
                for key, field in (
                    ("primaryModel", "primaryModel"),
                    ("fasterModel", "fasterModel"),
                ):
                    model = group.get(key)
                    if not isinstance(model, str):
                        continue
                    selected_provider = provider_name
                    if key == "fasterModel":
                        selected_provider = group.get("fasterProvider", provider_name)
                    ref = _profile_reference(
                        path,
                        selected_provider,
                        data["providers"].get(selected_provider, {}),
                        model,
                        field,
                    )
                    if ref:
                        entries.append(ref)
            return entries
        providers = data.get("providers", {})
        provider_name = data.get("provider", "default")
        provider = (
            providers.get(provider_name, {}) if isinstance(providers, dict) else {}
        )
        if not provider:
            provider = data
        entries = []
        primary = data.get("primaryModel")
        if isinstance(primary, dict) and primary.get("id"):
            primary_provider = dict(provider)
            primary_provider.setdefault("baseUrl", data.get("baseUrl", ""))
            primary_provider.setdefault("apiKey", data.get("apiKey", ""))
            ref = _profile_reference(
                path, provider_name, primary_provider, primary["id"], "primaryModel.id"
            )
            if ref:
                entries.append(ref)
        faster = data.get("fasterModel")
        if isinstance(faster, dict) and faster.get("id"):
            faster_provider_name = faster.get(
                "provider", data.get("fasterProvider", provider_name)
            )
            faster_provider = (
                providers.get(faster_provider_name, {})
                if isinstance(providers, dict)
                else {}
            )
            if not faster_provider and faster_provider_name == provider_name:
                faster_provider = provider
            faster_provider = dict(faster_provider)
            fallback_base = (
                data.get("baseUrl", "") if faster_provider_name == provider_name else ""
            )
            fallback_key = (
                data.get("apiKey", "") if faster_provider_name == provider_name else ""
            )
            faster_provider["baseUrl"] = faster.get(
                "baseUrl", faster_provider.get("baseUrl", fallback_base)
            )
            if "apiKeyEnv" in faster:
                faster_provider["apiKey"] = ""
                faster_provider["apiKeyEnv"] = faster["apiKeyEnv"]
            else:
                faster_provider["apiKey"] = faster.get(
                    "apiKey", faster_provider.get("apiKey", fallback_key)
                )
            ref = _profile_reference(
                path,
                faster_provider_name,
                faster_provider,
                faster["id"],
                "fasterModel.id",
            )
            if ref:
                entries.append(ref)
        return entries
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read Junie profile %s — skipping (%s)", path, exc)
        return None


def audit_junie_profiles():
    models_dir = Path(
        os.environ.get("JUNIE_MODELS_DIR", "~/.junie/models")
    ).expanduser()
    paths = (
        [
            path
            for path in models_dir.glob("*.json")
            if path.name != ".dotfiles-generated-profiles.json"
        ]
        if models_dir.is_dir()
        else []
    )
    local_config = Path(
        os.environ.get("JUNIE_LOCAL_GROUPS", "~/.junie-local/model-groups.json")
    ).expanduser()
    if local_config.exists():
        paths.append(local_config)
    references = []
    keys = {}
    base_urls = {}
    for path in paths:
        result = profile_models(path)
        if result:
            for entry in result:
                reference = entry["reference"]
                references.append(reference)
                keys[reference.endpoint] = entry["api_key"]
                base_urls[reference.endpoint] = entry["base_url"]
    catalogues = {}
    for endpoint in sorted({reference.endpoint for reference in references}):
        api_key = keys[endpoint]
        if api_key is None:
            catalogues[endpoint] = Catalogue(
                False, reason="credential is unset or uses an unsupported expression"
            )
            DRIFT_STATS["skipped"] += 1
            continue
        url = endpoint_models_url(base_urls[endpoint])
        DRIFT_STATS["checked"] += 1
        try:
            payload = get_catalogue(url, api_key)
            if not isinstance(payload, dict) or not isinstance(
                payload.get("data"), list
            ):
                raise ValueError("malformed catalogue response")
            ids = set()
            for item in payload["data"]:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("id"), str)
                    or not item["id"]
                ):
                    raise ValueError("malformed catalogue response")
                ids.add(item["id"])
            catalogues[endpoint] = Catalogue(True, frozenset(ids))
        except urllib.error.HTTPError as exc:
            DRIFT_STATS["skipped"] += 1
            catalogues[endpoint] = Catalogue(
                False, http_status=exc.code, reason=f"HTTP {exc.code}"
            )
        except Exception as exc:
            DRIFT_STATS["skipped"] += 1
            reason = (
                "malformed catalogue response"
                if isinstance(exc, ValueError)
                else "catalogue request failed"
            )
            catalogues[endpoint] = Catalogue(False, reason=reason)
        # Catalog ID shapes vary by provider (Meridian serves bare ids, Google's
        # OpenAI-compat catalog prefixes with "models/", profiles may carry
        # provider prefixes like "anthropic/..."). Compare on the last segment.

    comparison = compare_references(
        references, catalogues, normalize_id=_normalize_junie_wire_id
    )
    report = []
    violations = []
    for result in comparison.results:
        item = result.reference
        catalogue = catalogues.get(item.endpoint)
        report.append(
            {
                "outcome": result.outcome.value,
                "endpoint_namespace": item.endpoint.namespace,
                "credential_label": item.endpoint.credential_scope,
                "reference_path": item.source,
                "field": item.field,
                "wire_model_id": item.wire_id,
                "http_status": catalogue.http_status if catalogue else None,
                "reason": result.reason or (catalogue.reason if catalogue else None),
            }
        )
        if result.outcome == Outcome.MISSING:
            violations.append(
                f"{item.source} points to missing model {item.wire_id} at "
                f"{base_urls[item.endpoint]}"
            )
    return {"results": report, "complete": comparison.complete}, violations


def _normalize_junie_wire_id(endpoint, wire_id):
    """Normalize Google's catalogue prefix only for its exact endpoint host."""
    try:
        hostname = urlsplit(endpoint.namespace).hostname
    except (TypeError, ValueError):
        return wire_id
    if hostname == "generativelanguage.googleapis.com":
        return google_direct_id(wire_id)
    return wire_id


def check_junie_profiles() -> list[str]:
    return audit_junie_profiles()[1]


def _client_key(client: str) -> str | None:
    """Read only the authorised, private client key; never evaluate config commands."""
    home = Path.home()
    root = home / ".local/share/litellm/clients"
    target = root / f"{client}.key"
    try:
        current = home
        for component in (".local", "share", "litellm", "clients"):
            current = current / component
            if (
                current.is_symlink()
                or not current.is_dir()
                or current.stat().st_uid != os.getuid()
            ):
                return None
        if target.is_symlink() or not target.is_file():
            return None
        info = target.stat()
        if stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
            return None
        if root.resolve() != root or target.resolve() != target:
            return None
        value = target.read_text(encoding="utf-8").strip()
        return value or None
    except (OSError, RuntimeError):
        return None


def audit_client_providers():
    """Audit deployed OpenCode and Pi models routed through managed LiteLLM."""
    home = Path.home()
    port = _configured_litellm_port()
    endpoint_url = f"http://127.0.0.1:{port}/v1/models" if port else ""
    reports = []
    violations = []
    unknown = False
    sources = {
        "opencode": home / ".config/opencode/opencode.json",
        "pi": home / ".pi/agent/models.json",
    }
    inventories = {"opencode": {}, "pi": {}}
    pi_provider_names: set[str] = set()
    active_clients = {}
    for client, path in sources.items():
        active = bool(
            port
            and os.environ.get("DOTFILES_RUN_LITELLM_SETUP", "0") == "1"
            and os.environ.get(f"DOTFILES_{client.upper()}_USE_LITELLM", "0") == "1"
        )
        active_clients[client] = active
        if not active:
            reports.append(
                {
                    "outcome": Outcome.SKIPPED_INACTIVE.value,
                    "client": client,
                    "reference_path": str(path),
                    "field": "client gate",
                    "wire_model_id": None,
                    "reason": "client LiteLLM routing is inactive",
                }
            )
            continue
        if not path.exists():
            reports.append(
                {
                    "outcome": Outcome.UNKNOWN.value,
                    "client": client,
                    "reference_path": str(path),
                    "field": "<config>",
                    "wire_model_id": None,
                    "reason": "enabled client config is missing",
                }
            )
            unknown = True
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        key = _client_key(client)
        client_refs = []
        if not isinstance(data, dict):
            reports.append(
                {
                    "outcome": "UNKNOWN",
                    "client": client,
                    "reference_path": str(path),
                    "field": "<config>",
                    "wire_model_id": None,
                    "reason": "malformed client config",
                }
            )
            unknown = True
            continue
        providers = (
            data.get("provider") if client == "opencode" else data.get("providers")
        )
        if not isinstance(providers, dict) or not providers:
            reports.append(
                {
                    "outcome": "UNKNOWN",
                    "client": client,
                    "reference_path": str(path),
                    "field": "provider",
                    "wire_model_id": None,
                    "reason": "malformed provider inventory",
                }
            )
            unknown = True
            continue
        if client == "pi":
            pi_provider_names = set(providers)
        disabled_providers = (
            data.get("disabled_providers", []) if client == "opencode" else []
        )
        if not isinstance(disabled_providers, list) or any(
            not isinstance(name, str) for name in disabled_providers
        ):
            reports.append(
                {
                    "outcome": Outcome.UNKNOWN.value,
                    "client": client,
                    "reference_path": str(path),
                    "field": "disabled_providers",
                    "wire_model_id": None,
                    "reason": "malformed disabled provider list",
                }
            )
            unknown = True
            disabled_providers = []
        for provider_name in disabled_providers:
            if provider_name not in providers:
                reports.append(
                    {
                        "outcome": Outcome.SKIPPED_INACTIVE.value,
                        "client": client,
                        "reference_path": str(path),
                        "field": f"provider.{provider_name}",
                        "wire_model_id": None,
                        "reason": "provider disabled",
                    }
                )
        for provider_name, provider in providers.items():
            if not isinstance(provider, dict):
                reports.append(
                    {
                        "outcome": "UNKNOWN",
                        "client": client,
                        "reference_path": str(path),
                        "field": f"provider.{provider_name}",
                        "wire_model_id": None,
                        "reason": "malformed provider",
                    }
                )
                unknown = True
                continue
            if provider_name in disabled_providers or provider.get("disabled") is True:
                reports.append(
                    {
                        "outcome": Outcome.SKIPPED_INACTIVE.value,
                        "client": client,
                        "reference_path": str(path),
                        "field": f"provider.{provider_name}",
                        "wire_model_id": None,
                        "reason": "provider disabled",
                    }
                )
                continue
            base = (
                provider.get("options", {}).get("baseURL")
                if client == "opencode"
                and isinstance(provider.get("options", {}), dict)
                else provider.get("baseUrl")
            )
            try:
                parsed = urlsplit(base) if isinstance(base, str) else None
                routed = bool(
                    parsed
                    and port
                    and parsed.scheme == "http"
                    and parsed.hostname == "127.0.0.1"
                    and parsed.port == port
                    and parsed.path.rstrip("/") == "/v1"
                    and not parsed.username
                    and not parsed.password
                    and not parsed.query
                    and not parsed.fragment
                )
            except ValueError:
                parsed = None
                routed = False
            model_map = provider.get("models")
            if client == "pi" and isinstance(model_map, list):
                model_pairs = [
                    (m.get("id"), m) for m in model_map if isinstance(m, dict)
                ]
                if len(model_pairs) != len(model_map):
                    unknown = True
            elif isinstance(model_map, dict):
                model_pairs = [(name, item) for name, item in model_map.items()]
            else:
                reports.append(
                    {
                        "outcome": "UNKNOWN",
                        "client": client,
                        "reference_path": str(path),
                        "field": f"provider.{provider_name}.models",
                        "wire_model_id": None,
                        "reason": "malformed model inventory",
                    }
                )
                unknown = True
                continue
            if not routed:
                options = provider.get("options", {})
                if (
                    client == "opencode"
                    and provider_name in {"openai", "anthropic"}
                    and isinstance(options, dict)
                    and "baseURL" not in options
                ):
                    reason = "NATIVE_PROVIDER_NOT_PROXIED"
                elif isinstance(base, str) and base.startswith(("http://", "https://")):
                    reason = "DIRECT_NOT_PROXIED"
                else:
                    reason = "malformed or missing provider URL"
                reports.append(
                    {
                        "outcome": Outcome.UNKNOWN.value,
                        "client": client,
                        "reference_path": str(path),
                        "field": f"provider.{provider_name}",
                        "wire_model_id": None,
                        "reason": reason,
                    }
                )
                unknown = True
                continue
            scope = f"{client}:{provider_name}"
            for model_id, model in model_pairs:
                if (
                    not isinstance(model_id, str)
                    or not model_id
                    or not isinstance(model, dict)
                ):
                    unknown = True
                    reports.append(
                        {
                            "outcome": "UNKNOWN",
                            "client": client,
                            "reference_path": str(path),
                            "field": f"provider.{provider_name}.models",
                            "wire_model_id": None,
                            "reason": "malformed model entry",
                        }
                    )
                    continue
                client_refs.append(
                    (
                        provider_name,
                        model_id,
                        f"provider.{provider_name}.models.{model_id}",
                        scope,
                    )
                )
                inventories[client].setdefault(provider_name, set()).add(model_id)
        # Check each provider's credential expression and query independently.
        authorised = str(home / ".local/share/litellm/clients" / f"{client}.key")
        expected = (
            "{file:" + authorised + "}"
            if client == "opencode"
            else "!cat " + authorised
        )
        for provider_name in sorted({item[0] for item in client_refs}):
            provider = providers[provider_name]
            options = provider.get("options", {})
            raw = (
                options.get("apiKey")
                if client == "opencode" and isinstance(options, dict)
                else provider.get("apiKey")
            )
            provider_refs = [item for item in client_refs if item[0] == provider_name]
            if raw != expected or key is None:
                for _, model_id, source, scope in provider_refs:
                    reports.append(
                        {
                            "outcome": Outcome.UNKNOWN.value,
                            "client": client,
                            "endpoint_label": "litellm:127.0.0.1",
                            "credential_label": scope,
                            "reference_path": str(path),
                            "field": source,
                            "wire_model_id": model_id,
                            "reason": "authorised client key missing or unsupported reference",
                        }
                    )
                unknown = True
                continue
            endpoint = EndpointIdentity(
                "litellm:127.0.0.1", f"{client}:{provider_name}"
            )
            refs = [
                ModelReference(endpoint, model_id, str(path), field)
                for _, model_id, field, _ in provider_refs
            ]
            try:
                DRIFT_STATS["checked"] += 1
                payload = get_catalogue(endpoint_url, key)
                ids = payload.get("data") if isinstance(payload, dict) else None
                if not isinstance(ids, list) or any(
                    not isinstance(item, dict)
                    or not isinstance(item.get("id"), str)
                    or not item["id"]
                    for item in ids
                ):
                    raise ValueError("malformed catalogue")
                catalogue = Catalogue(True, frozenset(item["id"] for item in ids))
            except urllib.error.HTTPError as exc:
                catalogue = Catalogue(
                    False, http_status=exc.code, reason=f"HTTP {exc.code}"
                )
                DRIFT_STATS["skipped"] += 1
            except Exception:
                catalogue = Catalogue(False, reason="catalogue unavailable")
                DRIFT_STATS["skipped"] += 1
            for result in compare_references(refs, {endpoint: catalogue}).results:
                ref = result.reference
                reports.append(
                    {
                        "outcome": result.outcome.value,
                        "client": client,
                        "endpoint_label": "litellm:127.0.0.1",
                        "credential_label": endpoint.credential_scope,
                        "reference_path": ref.source,
                        "field": ref.field,
                        "wire_model_id": ref.wire_id,
                        "http_status": catalogue.http_status,
                        "reason": result.reason or catalogue.reason,
                    }
                )
                if result.outcome == Outcome.MISSING:
                    violations.append(
                        f"{client} {ref.source} points to missing proxy model {ref.wire_id}"
                    )
                if result.outcome == Outcome.UNKNOWN:
                    unknown = True
    settings_path = home / ".pi/agent/settings.json"
    if active_clients["pi"]:
        if not settings_path.exists():
            reports.append(
                {
                    "outcome": Outcome.UNKNOWN.value,
                    "client": "pi",
                    "reference_path": str(settings_path),
                    "field": "<settings>",
                    "wire_model_id": None,
                    "reason": "enabled Pi settings are missing",
                }
            )
            unknown = True
        else:
            try:
                settings = json.loads(settings_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                settings = None
            if not isinstance(settings, dict):
                reports.append(
                    {
                        "outcome": Outcome.UNKNOWN.value,
                        "client": "pi",
                        "reference_path": str(settings_path),
                        "field": "<settings>",
                        "wire_model_id": None,
                        "reason": "malformed Pi settings",
                    }
                )
                unknown = True
            else:
                subagents = settings.get("subagents", {})
                if not isinstance(subagents, dict):
                    reports.append(
                        {
                            "outcome": Outcome.UNKNOWN.value,
                            "client": "pi",
                            "reference_path": str(settings_path),
                            "field": "subagents",
                            "wire_model_id": None,
                            "reason": "malformed Pi subagents settings",
                        }
                    )
                    unknown = True
                    subagents = {}
                selections = [
                    (
                        "defaultModel",
                        settings.get("defaultProvider"),
                        settings.get("defaultModel"),
                        "defaultProvider" in settings,
                    ),
                    (
                        "subagents.defaultModel",
                        subagents.get(
                            "defaultProvider", settings.get("defaultProvider")
                        ),
                        subagents.get("defaultModel"),
                        "defaultProvider" in subagents,
                    ),
                ]
                overrides = subagents.get("agentOverrides", {})
                if isinstance(overrides, dict):
                    selections.extend(
                        (
                            f"subagents.agentOverrides.{role}.model",
                            value.get(
                                "provider",
                                subagents.get(
                                    "defaultProvider", settings.get("defaultProvider")
                                ),
                            ),
                            value.get("model"),
                            "provider" in value,
                        )
                        for role, value in overrides.items()
                        if isinstance(value, dict)
                    )
                    if any(not isinstance(value, dict) for value in overrides.values()):
                        unknown = True
                else:
                    reports.append(
                        {
                            "outcome": Outcome.UNKNOWN.value,
                            "client": "pi",
                            "reference_path": str(settings_path),
                            "field": "subagents.agentOverrides",
                            "wire_model_id": None,
                            "reason": "malformed agent override map",
                        }
                    )
                    unknown = True
                for field, provider, model, explicit_provider in selections:
                    if model is None:
                        continue
                    if isinstance(model, str) and "/" in model and not provider:
                        qualified_provider = model.split("/", 1)[0]
                        if qualified_provider in pi_provider_names:
                            provider = qualified_provider
                    if not isinstance(provider, str) or not isinstance(model, str):
                        reports.append(
                            {
                                "outcome": Outcome.UNKNOWN.value,
                                "client": "pi",
                                "reference_path": str(settings_path),
                                "field": field,
                                "wire_model_id": None,
                                "reason": "malformed model selection",
                            }
                        )
                        unknown = True
                        continue
                    qualified = model.split("/", 1)[0] if "/" in model else ""
                    if qualified in pi_provider_names:
                        if explicit_provider and provider and qualified != provider:
                            reports.append(
                                {
                                    "outcome": Outcome.UNKNOWN.value,
                                    "client": "pi",
                                    "reference_path": str(settings_path),
                                    "field": field,
                                    "wire_model_id": model,
                                    "reason": "qualified model conflicts with explicit provider",
                                }
                            )
                            unknown = True
                            continue
                        provider = qualified
                    inventory = inventories["pi"].get(provider)
                    selected_id = (
                        model[len(provider) + 1 :]
                        if model.startswith(provider + "/")
                        else model
                    )
                    if inventory is None:
                        outcome = Outcome.UNKNOWN
                        reason = "selected provider has no audited model inventory"
                        unknown = True
                    elif selected_id not in inventory:
                        outcome = Outcome.MISSING
                        reason = "selection absent from matching provider inventory"
                        violations.append(
                            f"Pi {field} selects a model absent from provider {provider} inventory"
                        )
                    else:
                        continue
                    reports.append(
                        {
                            "outcome": outcome.value,
                            "client": "pi",
                            "reference_path": str(settings_path),
                            "field": field,
                            "wire_model_id": model,
                            "reason": reason,
                        }
                    )
    return {"results": reports, "complete": not unknown}, violations


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(
        description="Check model assignments for catalog drift."
    )
    parser.add_argument(
        "--json", action="store_true", help="Print machine-readable results"
    )
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="Exit nonzero when catalogue evidence is incomplete",
    )
    args = parser.parse_args()
    results = {"violations": [], "warnings": []}
    try:
        data = json.loads(SLIM_PATH.read_text(encoding="utf-8"))
        violations = check_slim(data)
        results["violations"].extend(violations)
        results["violations"].extend(check_local_engine_models())
        # Local placeholders are intentionally not self-validated against the
        # same live set; deployed Junie profile checks validate concrete IDs.
    except (OSError, json.JSONDecodeError) as exc:
        results["violations"].append(f"Could not read slim config: {exc}")
    junie_audit, junie_violations = audit_junie_profiles()
    results["violations"].extend(junie_violations)
    client_audit, client_violations = audit_client_providers()
    results["violations"].extend(client_violations)
    stale, age = is_stale()
    if stale:
        age_text = "never" if age is None else f"{age:.0f}"
        results["warnings"].append(
            "Model assignments last synced "
            f"{age_text} days ago — re-run `make deploy` (or scripts/configure-jetbrains-ai.py + "
            "scripts/configure-opencode.py) after reviewing docs/MODEL_UPDATES.md"
        )
        logger.warning(results["warnings"][-1])
    if args.json:
        results["junie_audit"] = junie_audit
        results["client_audit"] = client_audit
        print(json.dumps(results, indent=2))
    else:
        for violation in results["violations"]:
            logger.error("Model drift: %s", violation)
        for item in junie_audit["results"]:
            if item["outcome"] == Outcome.UNKNOWN.value:
                logger.warning(
                    "Junie model audit UNKNOWN at %s for credential %s: %s",
                    item["endpoint_namespace"],
                    item["credential_label"],
                    item["reason"] or "catalogue unavailable",
                )
        for item in client_audit["results"]:
            if item["outcome"] == Outcome.UNKNOWN.value:
                logger.warning(
                    "%s model audit UNKNOWN at %s for credential %s: %s",
                    item.get("client"),
                    item.get("endpoint_label", "direct"),
                    item.get("credential_label", "unavailable"),
                    item.get("reason") or "catalogue unavailable",
                )
        logger.info(
            "Model drift check complete: %d violation(s); checked %d provider endpoint(s), skipped %d",
            len(results["violations"]),
            DRIFT_STATS["checked"],
            DRIFT_STATS["skipped"],
        )
    return (
        1
        if results["violations"]
        or (
            args.require_complete
            and not (junie_audit["complete"] and client_audit["complete"])
        )
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
