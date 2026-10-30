"""Collect strict, caller-authenticated direct provider model inventories."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

from free_preset_evidence import EvidenceError
from model_catalogues import endpoint_models_url, get_catalogue


class CatalogueCollectionError(EvidenceError):
    """Required route catalogue evidence could not be established."""


def _canonical_base(value):
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            return None
        port = parsed.port
    except (TypeError, ValueError):
        return None
    host = parsed.hostname.lower()
    if port and port != (443 if parsed.scheme == "https" else 80):
        host = f"{host}:{port}"
    return urlunsplit((parsed.scheme.lower(), host, parsed.path.rstrip("/"), "", ""))


def collect_free_route_catalogues(
    endpoints,
    *,
    trusted_endpoints,
    credentials,
    fetch=get_catalogue,
    clock=lambda: datetime.now(timezone.utc),
    ttl=timedelta(minutes=15),
):
    """Return a normalized-evidence direct_routes snapshot and trusted host map.

    Each endpoint explicitly supplies provider_id, upstream_host, and api_base.
    Credentials are caller-owned. Provider-specific protocols such as Cohere's
    native pagination must be supplied through a compatible fetch seam.
    """
    if not isinstance(ttl, timedelta) or ttl <= timedelta(0):
        raise CatalogueCollectionError("ttl must be positive")
    if ttl > timedelta(days=1):
        raise CatalogueCollectionError("ttl exceeds the one-day bound")
    if not isinstance(trusted_endpoints, dict):
        raise CatalogueCollectionError("caller-trusted endpoint map is required")
    if not isinstance(credentials, dict):
        raise CatalogueCollectionError(
            "caller-supplied credentials mapping is required"
        )

    routes = []
    bindings = {}
    windows = []
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            raise CatalogueCollectionError("endpoint record is malformed")
        provider = endpoint.get("provider_id")
        host = endpoint.get("upstream_host")
        base = _canonical_base(endpoint.get("api_base"))
        if (
            not isinstance(provider, str)
            or not provider.strip()
            or not isinstance(host, str)
            or not host.strip()
            or not base
        ):
            raise CatalogueCollectionError("endpoint identity is malformed")
        if provider != provider.strip() or host != host.strip():
            raise CatalogueCollectionError(
                "endpoint identity contains surrounding whitespace"
            )
        if trusted_endpoints.get(base) != host:
            raise CatalogueCollectionError("endpoint origin is not caller-trusted")
        # This collector implements OpenAI-compatible /models inventories only.
        if host == "cohere":
            raise CatalogueCollectionError(
                "Cohere native catalogue pagination is unsupported"
            )
        key = endpoint.get("credential_key")
        credential = credentials.get(key) if isinstance(key, str) and key else ""
        if endpoint.get("credential_required", True) and (
            not isinstance(credential, str) or not credential.strip()
        ):
            raise CatalogueCollectionError(
                f"required credential missing for provider {provider}"
            )
        url = endpoint_models_url(base)
        try:
            payload = fetch(url, credential or "", strict=True)
        except Exception as exc:
            raise CatalogueCollectionError(
                f"catalogue unavailable for provider {provider}"
            ) from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise CatalogueCollectionError(
                f"malformed catalogue for provider {provider}"
            )
        ids = []
        for item in data:
            model_id = item.get("id") if isinstance(item, dict) else None
            if not isinstance(model_id, str) or not model_id.strip():
                raise CatalogueCollectionError(
                    f"malformed model ID for provider {provider}"
                )
            ids.append(model_id)
        completed = clock()
        if (
            not isinstance(completed, datetime)
            or completed.tzinfo is None
            or completed.utcoffset() is None
        ):
            raise CatalogueCollectionError("clock must return an aware timestamp")
        completed = completed.astimezone(timezone.utc)
        expires = completed + ttl
        windows.append({"observed_at": completed, "expires_at": expires, "source": url})
        if len(ids) != len(set(ids)):
            raise CatalogueCollectionError(
                f"duplicate model ID for provider {provider}"
            )
        for model_id in sorted(ids):
            ref = f"{provider}/{model_id}"
            routes.append(
                {
                    "serving_ref": ref,
                    "provider_id": provider,
                    "model_id": model_id,
                    "upstream_model": ref,
                    "metadata_provider": provider,
                    "metadata_model_id": model_id,
                    "api_base": base,
                }
            )
            bindings[ref] = {
                "provider_id": provider,
                "upstream_host": host,
                "api_base": base,
            }

    if not windows:
        raise CatalogueCollectionError("at least one provider endpoint is required")
    observed = max(window["observed_at"] for window in windows)
    expires = min(window["expires_at"] for window in windows)
    if expires <= observed:
        raise CatalogueCollectionError("provider evidence windows do not overlap")
    return {
        "direct_routes": {
            "source": "; ".join(window["source"] for window in windows),
            "observed_at": observed.isoformat(),
            "expires_at": expires.isoformat(),
            "routes": routes,
            "provider_windows": [
                {
                    "source": window["source"],
                    "observed_at": window["observed_at"].isoformat(),
                    "expires_at": window["expires_at"].isoformat(),
                }
                for window in windows
            ],
        },
        "trusted_bindings": bindings,
    }
