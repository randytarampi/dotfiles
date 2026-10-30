"""Compose fresh runtime observations with the free-preset registry."""

from datetime import timedelta
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

from free_route_manifest import config_binding_digest
from free_preset_evidence import EvidenceError, normalize_free_preset_evidence
from free_gateway_evidence import validate_free_gateway_evidence
from free_preset_registry import PRESETS, materialize_free_presets
from litellm_config import _live_catalogue
from models_dev import fetch_models_dev_snapshot
from provider_endpoints import PROVIDER_ENDPOINTS


class DynamicFreeRegistryError(ValueError):
    """Runtime evidence could not safely materialize a requested preset."""


DIRECT_PROVIDERS = ("opencode", "openrouter", "poolside", "cohere")
CATALOGUE_PROVIDERS = {"opencode": "opencode", "openrouter": "openrouter"}
CONSUMERS = frozenset({"opencode", "pi", "junie"})


def _consumer_key(environ, consumer):
    if consumer not in CONSUMERS:
        raise DynamicFreeRegistryError("unsupported gateway consumer")
    value = environ.get(f"LITELLM_{consumer.upper()}_KEY")
    if not isinstance(value, str) or not value.strip():
        raise DynamicFreeRegistryError("gateway client credential unavailable")
    return value.strip()


def _safe_owned_file(path, *, private=False):
    """Reject symlinked ancestors and non-private or multiply-linked files."""
    path = Path(path).expanduser().absolute()
    cursor = Path(path.anchor)
    for part in path.parts[1:]:
        cursor = cursor / part
        if cursor.is_symlink():
            raise DynamicFreeRegistryError("gateway evidence path is unsafe")
    try:
        metadata = path.lstat()
    except OSError as error:
        raise DynamicFreeRegistryError("gateway evidence file unavailable") from error
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise DynamicFreeRegistryError("gateway evidence file is unsafe")
    if metadata.st_uid != os.getuid() or (
        private and stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise DynamicFreeRegistryError(
            "gateway evidence file has unsafe ownership or mode"
        )
    return path


def load_gateway_free_evidence(environ, consumer="opencode"):
    """Load trusted credential-free gateway provenance and a fresh alias list."""
    if consumer not in CONSUMERS:
        raise DynamicFreeRegistryError("unsupported gateway consumer")
    port = environ.get("LITELLM_PORT", "4000")
    if not isinstance(port, str) or not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise DynamicFreeRegistryError("gateway port is invalid")
    root = (
        Path(environ.get("LITELLM_ROOT", "~/.local/share/litellm")).expanduser()
        / "config.yaml"
    )
    manifest_path = root.with_name("free-route-manifest.json")
    projection_path = root.with_name("free-route-config.json")
    root = _safe_owned_file(root)
    manifest_path = _safe_owned_file(manifest_path)
    projection_path = _safe_owned_file(projection_path)
    try:
        import yaml

        current_config = yaml.safe_load(root.read_text(encoding="utf-8"))
    except Exception as error:
        raise DynamicFreeRegistryError("current gateway config is malformed") from error
    if not isinstance(current_config, dict) or not isinstance(
        current_config.get("model_list"), list
    ):
        raise DynamicFreeRegistryError("current gateway config is malformed")
    current_routes = current_config["model_list"]
    if any(
        not isinstance(row, dict)
        or not isinstance(row.get("model_name"), str)
        or not isinstance(row.get("litellm_params"), dict)
        for row in current_routes
    ):
        raise DynamicFreeRegistryError("current gateway config is malformed")
    key_path = _safe_owned_file(
        root.parent / "clients" / f"{consumer}.key", private=True
    )
    key = key_path.read_text(encoding="utf-8").strip()
    if not key:
        raise DynamicFreeRegistryError("gateway client credential unavailable")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    projection = json.loads(projection_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(projection, dict):
        raise DynamicFreeRegistryError("gateway evidence files are malformed")
    routes = projection.get("routes")
    if not isinstance(routes, list):
        raise DynamicFreeRegistryError(
            "trusted gateway route projection is unavailable"
        )
    if manifest.get("config_binding_sha256") != projection.get("config_binding_sha256"):
        raise DynamicFreeRegistryError("gateway route projection binding mismatch")
    try:
        current_binding = config_binding_digest(current_routes)
    except (AttributeError, TypeError, ValueError) as error:
        raise DynamicFreeRegistryError("current gateway config is malformed") from error
    if current_binding != manifest.get("config_binding_sha256"):
        raise DynamicFreeRegistryError("current config route binding mismatch")
    route_projection = [
        {
            "model_name": row.get("model_name"),
            "litellm_params": row.get("litellm_params"),
        }
        for row in routes
        if isinstance(row, dict)
    ]
    if len(route_projection) != len(routes) or config_binding_digest(
        route_projection
    ) != projection.get("config_binding_sha256"):
        raise DynamicFreeRegistryError("gateway route projection digest mismatch")
    from model_catalogues import get_catalogue

    endpoint = f"http://127.0.0.1:{port}/v1"
    payload = get_catalogue(f"{endpoint}/models", key, strict=True)
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list) or any(
        not isinstance(row, dict) or not isinstance(row.get("id"), str) for row in data
    ):
        raise DynamicFreeRegistryError(
            "gateway advertised model catalogue is malformed"
        )
    now = datetime.now(timezone.utc)
    return {
        "manifest": manifest,
        "expected_config_routes": routes,
        "gateway_models": {
            "source": "authenticated LiteLLM /v1/models",
            "observed_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=5)).isoformat(),
            "models": [row["id"] for row in data],
        },
        "trusted_endpoints": {
            endpoint["baseUrl"].rstrip("/"): provider
            for provider, endpoint in PROVIDER_ENDPOINTS.items()
        },
        "trusted_native": _trusted_native(routes),
    }


def _trusted_native(routes):
    native = {}
    for route in routes:
        provenance = route.get("_route_provenance", {})
        params = route.get("litellm_params", {})
        provider = provenance.get("metadata_provider")
        model = params.get("model")
        if provider in {"cohere_chat"}:
            provider = "cohere"
        host = provenance.get("upstream_host")
        if (
            provider in {"cohere", "poolside"}
            and host == provider
            and isinstance(model, str)
        ):
            native[(route.get("model_name"), model)] = provider
    return native


def _complete_free_schema(registry, requested):
    """Supply stable defaults only for optional registry routes not required by consumers."""
    import copy

    result = copy.deepcopy(registry)
    for preset in requested:
        if preset not in result.get("presets", {}) or preset not in result.get(
            "_tiers", {}
        ):
            continue
        roles = result["presets"][preset]
        tier = result["_tiers"][preset]
        for name in ("coder", "task_worker"):
            if name in roles:
                roles[name]["model"] = roles["fixer"]["model"]
        fallback = tier.setdefault("fallback", {})
        for name in ("coder", "task_worker"):
            if name in roles:
                fallback.setdefault(name, list(fallback.get("fixer", [])))
    return result


def _direct_route_snapshot(environ, route_fetch, clock):
    routes = []
    failures = {}
    trusted_native = {}
    trusted_endpoints = {}
    observations = []
    for provider in DIRECT_PROVIDERS:
        endpoint = PROVIDER_ENDPOINTS[provider]
        key = environ.get(endpoint["apiKeyEnv"])
        if not isinstance(key, str) or not key.strip():
            failures[provider] = "credential unavailable; provider excluded"
            continue
        try:
            if provider == "cohere":
                models = route_fetch(provider, key, endpoint["baseUrl"])
                metadata_provider = "cohere"
            elif provider == "poolside":
                models = route_fetch(provider, key, endpoint["baseUrl"])
                metadata_provider = "poolside"
            else:
                models = route_fetch(provider, key, endpoint["baseUrl"])
                metadata_provider = CATALOGUE_PROVIDERS[provider]
            if not isinstance(models, (list, tuple)) or any(
                not isinstance(model, str) or not model.strip() for model in models
            ):
                raise DynamicFreeRegistryError("malformed provider catalogue")
            observed = clock()
            observations.append(observed)
            for model in sorted(set(models)):
                routes.append(
                    {
                        "serving_ref": f"{provider}/{model}",
                        "provider_id": metadata_provider,
                        "model_id": model,
                        "metadata_provider": metadata_provider,
                        "metadata_model_id": model,
                        "upstream_model": f"{provider}/{model}",
                        "api_base": endpoint["baseUrl"].rstrip("/"),
                    }
                )
            trusted_endpoints[endpoint["baseUrl"].rstrip("/")] = provider
        except Exception:
            failures[provider] = "catalogue observation unavailable"
    if not routes:
        raise DynamicFreeRegistryError(
            "no configured direct provider catalogue was successfully observed"
        )
    now = max(observations)
    expires = min(observations) + timedelta(minutes=15)
    if expires <= clock():
        raise DynamicFreeRegistryError("provider catalogue observations expired")
    return (
        {
            "source": "direct provider catalogues",
            "observed_at": now.isoformat(),
            "expires_at": expires.isoformat(),
            "routes": routes,
        },
        trusted_native,
        trusted_endpoints,
        failures,
    )


def prepare_dynamic_free_registry(
    registry,
    requested_presets,
    environ,
    *,
    metadata_fetch=None,
    route_fetch=None,
    clock=None,
    gateway_evidence=None,
    gateway_evidence_loader=None,
    consumer="opencode",
):
    """Fetch runtime evidence and atomically materialize requested free presets.

    Injected boundaries keep tests synthetic; production defaults use fresh
    models.dev/provider catalogues and require a caller-supplied gateway loader.
    """
    from datetime import datetime, timezone

    metadata_fetch = metadata_fetch or fetch_models_dev_snapshot
    clock = clock or (lambda: datetime.now(timezone.utc))
    route_fetch = route_fetch or _live_catalogue
    if environ.get("DOTFILES_USE_LITELLM_PROXY", "0") == "1":
        if gateway_evidence is None and gateway_evidence_loader is not None:
            gateway_evidence = (
                gateway_evidence_loader(environ)
                if consumer == "opencode"
                else gateway_evidence_loader(environ, consumer=consumer)
            )
        if gateway_evidence is None and gateway_evidence_loader is None:
            gateway_evidence = load_gateway_free_evidence(environ, consumer=consumer)
        if gateway_evidence is None:
            raise DynamicFreeRegistryError(
                "gateway mode refused: no validated secret-free manifest evidence loader"
            )
    try:
        models = metadata_fetch()
        if not isinstance(models, dict) or not isinstance(models.get("models"), dict):
            raise EvidenceError("fresh models.dev snapshot is unavailable")
        now = clock()
        if environ.get("DOTFILES_USE_LITELLM_PROXY", "0") == "1":
            required = {
                "manifest",
                "expected_config_routes",
                "gateway_models",
                "trusted_endpoints",
                "trusted_native",
            }
            if not isinstance(gateway_evidence, dict) or not required.issubset(
                gateway_evidence
            ):
                raise EvidenceError("gateway evidence bundle is incomplete")
            candidates = validate_free_gateway_evidence(
                **gateway_evidence,
                models_dev=models,
                now=now,
            )
            failures = {}
        else:
            direct, native, trusted, failures = _direct_route_snapshot(
                environ, route_fetch, clock
            )
            now = clock()
            candidates = normalize_free_preset_evidence(
                models_dev=models,
                direct_routes=direct,
                gateway=None,
                trusted_endpoints=trusted,
                trusted_native=native,
                now=now,
            )
        requested = requested_presets or PRESETS
        transformed, audits = materialize_free_presets(
            _complete_free_schema(registry, requested), candidates, requested, now=now
        )
    except (EvidenceError, DynamicFreeRegistryError) as error:
        raise DynamicFreeRegistryError(str(error)) from error
    return transformed, {"presets": audits, "provider_failures": failures}
