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
    google_direct_id,  # noqa: E402  # imported after scripts/lib path bootstrap.
)

REPO_ROOT = SCRIPT_DIR.parent
DRIFT_STATS = {"checked": 0, "skipped": 0}
MALFORMED_CATALOGUE_REASON = "malformed catalogue response"
CATALOGUE_UNAVAILABLE_REASON = "catalogue unavailable"
LITELLM_ENDPOINT_LABEL = "litellm:127.0.0.1"
INVALID_ENDPOINT_NAMESPACE = "invalid-endpoint"
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
    try:
        parsed = urlsplit(endpoint_models_url(base_url))
        host = parsed.hostname or "unknown-host"
        if parsed.port:
            host += f":{parsed.port}"
    except ValueError:
        return INVALID_ENDPOINT_NAMESPACE
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
    if namespace == INVALID_ENDPOINT_NAMESPACE:
        key = None
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
            return _group_profile_models(path, data)
        return _single_profile_models(path, data)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read Junie profile %s — skipping (%s)", path, exc)
        return None


def _group_profile_models(path: Path, data: dict) -> list[dict]:
    entries = []
    for group in data["groups"].values():
        if not isinstance(group, dict):
            continue
        provider_name = group.get("provider", "")
        for model_key, field in (
            ("primaryModel", "primaryModel"),
            ("fasterModel", "fasterModel"),
        ):
            model = group.get(model_key)
            if not isinstance(model, str):
                continue
            selected_provider = (
                group.get("fasterProvider", provider_name)
                if model_key == "fasterModel"
                else provider_name
            )
            reference = _profile_reference(
                path,
                selected_provider,
                data["providers"].get(selected_provider, {}),
                model,
                field,
            )
            if reference:
                entries.append(reference)
    return entries


def _single_profile_models(path: Path, data: dict) -> list[dict]:
    providers = data.get("providers", {})
    provider_name = data.get("provider", "default")
    provider = providers.get(provider_name, {}) if isinstance(providers, dict) else {}
    if not provider:
        provider = data
    entries = []
    primary = _single_primary_reference(path, data, provider_name, provider)
    if primary:
        entries.append(primary)
    faster = _single_faster_reference(path, data, provider_name, provider, providers)
    if faster:
        entries.append(faster)
    return entries


def _single_primary_reference(path, data, provider_name, provider):
    primary = data.get("primaryModel")
    if not isinstance(primary, dict) or not primary.get("id"):
        return None
    primary_provider = dict(provider)
    primary_provider.setdefault("baseUrl", data.get("baseUrl", ""))
    primary_provider.setdefault("apiKey", data.get("apiKey", ""))
    return _profile_reference(
        path, provider_name, primary_provider, primary["id"], "primaryModel.id"
    )


def _single_faster_reference(path, data, provider_name, provider, providers):
    faster = data.get("fasterModel")
    if not isinstance(faster, dict) or not faster.get("id"):
        return None
    faster_provider_name = faster.get(
        "provider", data.get("fasterProvider", provider_name)
    )
    selected_provider = (
        providers.get(faster_provider_name, {}) if isinstance(providers, dict) else {}
    )
    if not selected_provider and faster_provider_name == provider_name:
        selected_provider = provider
    return _single_faster_profile_reference(
        path, data, faster, faster_provider_name, selected_provider, provider_name
    )


def _single_faster_profile_reference(
    path, data, faster, faster_provider_name, faster_provider, provider_name
):
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
    return _profile_reference(
        path,
        faster_provider_name,
        faster_provider,
        faster["id"],
        "fasterModel.id",
    )


def audit_junie_profiles():
    paths = _junie_profile_paths()
    references, keys, base_urls = _collect_junie_references(paths)
    catalogues = _fetch_junie_catalogues(references, keys, base_urls)
    return _format_junie_audit(references, catalogues, base_urls)


def _junie_profile_paths():
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
    return paths


def _collect_junie_references(paths):
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
    return references, keys, base_urls


def _fetch_junie_catalogues(references, keys, base_urls):
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
                raise ValueError(MALFORMED_CATALOGUE_REASON)
            ids = set()
            for item in payload["data"]:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("id"), str)
                    or not item["id"]
                ):
                    raise ValueError(MALFORMED_CATALOGUE_REASON)
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
                MALFORMED_CATALOGUE_REASON
                if isinstance(exc, ValueError)
                else "catalogue request failed"
            )
            catalogues[endpoint] = Catalogue(False, reason=reason)
    return catalogues


def _format_junie_audit(references, catalogues, base_urls):
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
    value = None
    try:
        current = home
        safe_parents = True
        for component in (".local", "share", "litellm", "clients"):
            current = current / component
            if (
                current.is_symlink()
                or not current.is_dir()
                or current.stat().st_uid != os.getuid()
            ):
                safe_parents = False
                break
        if safe_parents and not target.is_symlink() and target.is_file():
            info = target.stat()
            if (
                stat.S_IMODE(info.st_mode) == 0o600
                and info.st_uid == os.getuid()
                and root.resolve() == root
                and target.resolve() == target
            ):
                value = target.read_text(encoding="utf-8").strip() or None
    except (OSError, RuntimeError):
        pass
    return value


def audit_client_providers():
    """Audit deployed OpenCode and Pi models routed through managed LiteLLM."""
    port = _configured_litellm_port()
    results = [
        _audit_client(client, path, port) for client, path in _client_paths().items()
    ]
    reports = [item for result in results for item in result["results"]]
    violations = [item for result in results for item in result["violations"]]
    return {
        "results": reports,
        "complete": all(result["complete"] for result in results),
    }, violations


def _client_paths():
    home = Path.home()
    return {
        "opencode": home / ".config/opencode/opencode.json",
        "pi": home / ".pi/agent/models.json",
    }


def _client_is_active(client, port):
    return bool(
        port
        and os.environ.get("DOTFILES_RUN_LITELLM_SETUP", "0") == "1"
        and os.environ.get(f"DOTFILES_{client.upper()}_USE_LITELLM", "0") == "1"
    )


def _audit_client(client, path, port):
    if not _client_is_active(client, port):
        return _client_result(
            [
                _client_report(
                    client,
                    path,
                    "client gate",
                    "client LiteLLM routing is inactive",
                    Outcome.SKIPPED_INACTIVE,
                )
            ],
            complete=True,
        )
    if not path.exists():
        return _client_result(
            [
                _client_report(
                    client, path, "<config>", "enabled client config is missing"
                )
            ]
        )
    data = _read_json_object(path)
    if data is None:
        return _client_result(
            [_client_report(client, path, "<config>", "malformed client config")]
        )
    providers = data.get("provider") if client == "opencode" else data.get("providers")
    if not isinstance(providers, dict) or not providers:
        return _client_result(
            [_client_report(client, path, "provider", "malformed provider inventory")]
        )
    key = _client_key(client)
    inventory = {}
    reports, violations, unknown = _audit_providers(
        client, path, data, providers, port, key, inventory
    )
    if client == "pi":
        pi_reports, pi_violations, pi_complete = _audit_pi_settings(
            path.with_name("settings.json"), inventory, set(providers)
        )
        reports.extend(pi_reports)
        violations.extend(pi_violations)
        unknown = unknown or not pi_complete
    return _client_result(reports, violations, complete=not unknown)


def _client_result(reports, violations=None, *, complete=False):
    return {"results": reports, "violations": violations or [], "complete": complete}


def _read_json_object(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _client_report(client, path, field, reason, outcome=Outcome.UNKNOWN):
    return {
        "outcome": outcome.value,
        "client": client,
        "reference_path": str(path),
        "field": field,
        "wire_model_id": None,
        "reason": reason,
    }


def _audit_providers(client, path, data, providers, port, key, inventory):
    reports = []
    violations = []
    unknown = False
    disabled, invalid_disabled = _disabled_provider_names(client, data)
    if invalid_disabled:
        reports.append(
            _client_report(
                client, path, "disabled_providers", "malformed disabled provider list"
            )
        )
        unknown = True
    for provider_name in disabled - providers.keys():
        reports.append(
            _client_report(
                client,
                path,
                f"provider.{provider_name}",
                "provider disabled",
                Outcome.SKIPPED_INACTIVE,
            )
        )
    provider_refs = []
    for name, provider in providers.items():
        provider_result = _provider_references(
            client, path, name, provider, disabled, port, inventory
        )
        reports.extend(provider_result["results"])
        provider_refs.extend(provider_result["references"])
        unknown = unknown or not provider_result["complete"]
    authorised = str(Path.home() / ".local/share/litellm/clients" / f"{client}.key")
    expected_key_ref = (
        f"{{file:{authorised}}}" if client == "opencode" else f"!cat {authorised}"
    )
    for provider_name, provider, references in provider_refs:
        provider_reports, provider_violations, provider_complete = (
            _compare_client_provider(
                client, path, provider_name, provider, references, key, expected_key_ref
            )
        )
        reports.extend(provider_reports)
        violations.extend(provider_violations)
        unknown = unknown or not provider_complete
    return reports, violations, unknown


def _disabled_provider_names(client, data):
    disabled = data.get("disabled_providers", []) if client == "opencode" else []
    valid = isinstance(disabled, list) and all(
        isinstance(name, str) for name in disabled
    )
    return (set(disabled) if valid else set()), not valid


def _provider_references(client, path, name, provider, disabled, port, inventory):
    if not isinstance(provider, dict):
        return {
            "results": [
                _client_report(client, path, f"provider.{name}", "malformed provider")
            ],
            "references": [],
            "complete": False,
        }
    if name in disabled or provider.get("disabled") is True:
        return {
            "results": [
                _client_report(
                    client,
                    path,
                    f"provider.{name}",
                    "provider disabled",
                    Outcome.SKIPPED_INACTIVE,
                )
            ],
            "references": [],
            "complete": True,
        }
    model_pairs, malformed_inventory, valid_shape = _provider_models(
        client, provider.get("models")
    )
    if not valid_shape:
        return {
            "results": [
                _client_report(
                    client, path, f"provider.{name}.models", "malformed model inventory"
                )
            ],
            "references": [],
            "complete": False,
        }
    base_url = _provider_base_url(client, provider)
    if not _is_litellm_url(base_url, port):
        reason = _direct_route_reason(client, name, provider, base_url)
        return {
            "results": [_client_report(client, path, f"provider.{name}", reason)],
            "references": [],
            "complete": False,
        }
    references = []
    reports = []
    complete = not malformed_inventory
    if malformed_inventory:
        reports.append(
            _client_report(
                client, path, f"provider.{name}.models", "malformed model entry"
            )
        )
    for model_id, model in model_pairs:
        if not isinstance(model_id, str) or not model_id or not isinstance(model, dict):
            reports.append(
                _client_report(
                    client, path, f"provider.{name}.models", "malformed model entry"
                )
            )
            continue
        references.append((model_id, f"provider.{name}.models.{model_id}"))
        inventory.setdefault(name, set()).add(model_id)
    complete = complete and len(references) == len(model_pairs)
    return {
        "results": reports,
        "references": [(name, provider, references)] if references else [],
        "complete": complete,
    }


def _provider_models(client, model_map):
    if client == "pi" and isinstance(model_map, list):
        pairs = [
            (model.get("id"), model) for model in model_map if isinstance(model, dict)
        ]
        return pairs, len(pairs) != len(model_map), True
    if isinstance(model_map, dict):
        return list(model_map.items()), False, True
    return [], True, False


def _provider_base_url(client, provider):
    options = provider.get("options", {})
    if client == "opencode" and isinstance(options, dict):
        return options.get("baseURL")
    return provider.get("baseUrl")


def _is_litellm_url(base_url, port):
    try:
        parsed = urlsplit(base_url) if isinstance(base_url, str) else None
        return bool(
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
        return False


def _direct_route_reason(client, provider_name, provider, base_url):
    options = provider.get("options", {})
    if (
        client == "opencode"
        and provider_name in {"openai", "anthropic"}
        and isinstance(options, dict)
        and "baseURL" not in options
    ):
        return "NATIVE_PROVIDER_NOT_PROXIED"
    if isinstance(base_url, str) and base_url.startswith(("http://", "https://")):
        return "DIRECT_NOT_PROXIED"
    return "malformed or missing provider URL"


def _compare_client_provider(
    client, path, name, provider, references, key, expected_key_ref
):
    options = provider.get("options", {})
    key_ref = (
        options.get("apiKey")
        if client == "opencode" and isinstance(options, dict)
        else provider.get("apiKey")
    )
    scope = f"{client}:{name}"
    if key_ref != expected_key_ref or key is None:
        return (
            [
                {
                    "outcome": Outcome.UNKNOWN.value,
                    "client": client,
                    "endpoint_label": LITELLM_ENDPOINT_LABEL,
                    "credential_label": scope,
                    "reference_path": str(path),
                    "field": field,
                    "wire_model_id": model_id,
                    "reason": "authorised client key missing or unsupported reference",
                }
                for model_id, field in references
            ],
            [],
            False,
        )
    endpoint = EndpointIdentity(LITELLM_ENDPOINT_LABEL, scope)
    model_refs = [
        ModelReference(endpoint, model_id, str(path), field)
        for model_id, field in references
    ]
    catalogue = _fetch_client_catalogue(key)
    return _format_client_comparison(client, endpoint, model_refs, catalogue)


def _fetch_client_catalogue(key):
    try:
        DRIFT_STATS["checked"] += 1
        payload = get_catalogue(_client_models_url(), key)
        ids = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(ids, list) or any(
            not isinstance(item, dict)
            or not isinstance(item.get("id"), str)
            or not item["id"]
            for item in ids
        ):
            raise ValueError("malformed catalogue")
        return Catalogue(True, frozenset(item["id"] for item in ids))
    except urllib.error.HTTPError as exc:
        DRIFT_STATS["skipped"] += 1
        return Catalogue(False, http_status=exc.code, reason=f"HTTP {exc.code}")
    except Exception:
        DRIFT_STATS["skipped"] += 1
        return Catalogue(False, reason=CATALOGUE_UNAVAILABLE_REASON)


def _client_models_url():
    port = _configured_litellm_port()
    return f"http://127.0.0.1:{port}/v1/models"


def _format_client_comparison(client, endpoint, references, catalogue):
    reports = []
    violations = []
    comparison = compare_references(references, {endpoint: catalogue})
    for result in comparison.results:
        ref = result.reference
        reports.append(
            {
                "outcome": result.outcome.value,
                "client": client,
                "endpoint_label": LITELLM_ENDPOINT_LABEL,
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
    return reports, violations, comparison.complete


def _audit_pi_settings(settings_path, inventories, provider_names):
    if not settings_path.exists():
        return (
            [
                _client_report(
                    "pi", settings_path, "<settings>", "enabled Pi settings are missing"
                )
            ],
            [],
            False,
        )
    settings = _read_json_object(settings_path)
    if settings is None:
        return (
            [
                _client_report(
                    "pi", settings_path, "<settings>", "malformed Pi settings"
                )
            ],
            [],
            False,
        )
    selections, reports, complete = _pi_settings_selections(settings_path, settings)
    violations = []
    for selection in selections:
        selection_reports, violation, selection_complete = _compare_pi_selection(
            settings_path, selection, inventories, provider_names
        )
        reports.extend(selection_reports)
        if violation:
            violations.append(violation)
        complete = complete and selection_complete
    return reports, violations, complete


def _pi_settings_selections(settings_path, settings):
    reports = []
    subagents = settings.get("subagents", {})
    if not isinstance(subagents, dict):
        reports.append(
            _client_report(
                "pi", settings_path, "subagents", "malformed Pi subagents settings"
            )
        )
        return [], reports, False
    selections = [
        (
            "defaultModel",
            settings.get("defaultProvider"),
            settings.get("defaultModel"),
            "defaultProvider" in settings,
        ),
        (
            "subagents.defaultModel",
            subagents.get("defaultProvider", settings.get("defaultProvider")),
            subagents.get("defaultModel"),
            "defaultProvider" in subagents,
        ),
    ]
    overrides = subagents.get("agentOverrides", {})
    if not isinstance(overrides, dict):
        reports.append(
            _client_report(
                "pi",
                settings_path,
                "subagents.agentOverrides",
                "malformed agent override map",
            )
        )
        return selections, reports, False
    for role, value in overrides.items():
        if not isinstance(value, dict):
            reports.append(
                _client_report(
                    "pi",
                    settings_path,
                    f"subagents.agentOverrides.{role}",
                    "malformed agent override",
                )
            )
            continue
        selections.append(
            (
                f"subagents.agentOverrides.{role}.model",
                value.get(
                    "provider",
                    subagents.get("defaultProvider", settings.get("defaultProvider")),
                ),
                value.get("model"),
                "provider" in value,
            )
        )
    return selections, reports, not reports


def _compare_pi_selection(settings_path, selection, inventories, provider_names):
    field, provider, model, explicit_provider = selection
    if model is None:
        return [], None, True
    if not isinstance(model, str) or not isinstance(provider, str):
        return (
            [_client_report("pi", settings_path, field, "malformed model selection")],
            None,
            False,
        )
    qualified = model.split("/", 1)[0] if "/" in model else ""
    if qualified in provider_names:
        if explicit_provider and provider and qualified != provider:
            return (
                [
                    _client_report(
                        "pi",
                        settings_path,
                        field,
                        "qualified model conflicts with explicit provider",
                    )
                ],
                None,
                False,
            )
        provider = qualified
    elif not provider and qualified:
        return (
            [
                _client_report(
                    "pi", settings_path, field, "selected provider is unresolved"
                )
            ],
            None,
            False,
        )
    selected_id = (
        model[len(provider) + 1 :] if model.startswith(provider + "/") else model
    )
    inventory = inventories.get(provider)
    if inventory is None:
        return (
            [
                _client_report(
                    "pi",
                    settings_path,
                    field,
                    "selected provider has no audited model inventory",
                )
            ],
            None,
            False,
        )
    if selected_id in inventory:
        return [], None, True
    report = _client_report(
        "pi",
        settings_path,
        field,
        "selection absent from matching provider inventory",
        Outcome.MISSING,
    )
    report["wire_model_id"] = model
    violation = f"Pi {field} selects a model absent from provider {provider} inventory"
    return [report], violation, True


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
    results = {"violations": _model_assignment_violations(), "warnings": []}
    junie_audit, junie_violations = audit_junie_profiles()
    results["violations"].extend(junie_violations)
    client_audit, client_violations = audit_client_providers()
    results["violations"].extend(client_violations)
    _record_stale_model_warning(results)
    _display_model_drift_results(args, results, junie_audit, client_audit)
    return _model_drift_exit_status(args, results, junie_audit, client_audit)


def _model_assignment_violations():
    violations = []
    try:
        data = json.loads(SLIM_PATH.read_text(encoding="utf-8"))
        violations.extend(check_slim(data))
        violations.extend(check_local_engine_models())
        # Local placeholders are intentionally not self-validated against the
        # same live set; deployed Junie profile checks validate concrete IDs.
    except (OSError, json.JSONDecodeError) as exc:
        violations.append(f"Could not read slim config: {exc}")
    return violations


def _record_stale_model_warning(results):
    stale, age = is_stale()
    if stale:
        age_text = "never" if age is None else f"{age:.0f}"
        results["warnings"].append(
            "Model assignments last synced "
            f"{age_text} days ago — re-run `make deploy` (or scripts/configure-jetbrains-ai.py + "
            "scripts/configure-opencode.py) after reviewing docs/MODEL_UPDATES.md"
        )
        logger.warning(results["warnings"][-1])


def _display_model_drift_results(args, results, junie_audit, client_audit):
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
                    item["reason"] or CATALOGUE_UNAVAILABLE_REASON,
                )
        for item in client_audit["results"]:
            if item["outcome"] == Outcome.UNKNOWN.value:
                logger.warning(
                    "%s model audit UNKNOWN at %s for credential %s: %s",
                    item.get("client"),
                    item.get("endpoint_label", "direct"),
                    item.get("credential_label", "unavailable"),
                    item.get("reason") or CATALOGUE_UNAVAILABLE_REASON,
                )
        logger.info(
            "Model drift check complete: %d violation(s); checked %d provider endpoint(s), skipped %d",
            len(results["violations"]),
            DRIFT_STATS["checked"],
            DRIFT_STATS["skipped"],
        )


def _model_drift_exit_status(args, results, junie_audit, client_audit):
    if results["violations"]:
        return 1
    if args.require_complete and not (
        junie_audit["complete"] and client_audit["complete"]
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
