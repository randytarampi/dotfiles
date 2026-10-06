"""Shared authenticated model-catalogue accessors."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import os
from pathlib import Path
from urllib.parse import urlsplit

from constants import (
    BASE_URLS,
    MERIDIAN_DEFAULT_PORT,
    MERIDIAN_PORT_ENV,
    OLLAMA_HOST_ENV,
    OLLAMA_LOCAL_DEFAULT_PORT,
    OLLAMA_LOCAL_PORT_ENV,
    OMLX_PORT_ENV,
    OMLX_BASE_URL_ENV,
    PROVIDER_BASE_URL_ENVS,
)

import logger

BASE_URL_HOSTS = frozenset(
    parsed.hostname
    for parsed in (urlsplit(url) for url in BASE_URLS.values())
    if parsed.hostname
)
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
TRUSTED_HTTPS_HOSTS = BASE_URL_HOSTS | {
    "api.githubcopilot.com",
    "generativelanguage.googleapis.com",
    "opencode.ai",
    "open.openaipublic.com",
}


def _origin(url):
    parsed = urlsplit(url)
    return (
        parsed.scheme.lower(),
        parsed.hostname.lower() if parsed.hostname else None,
        parsed.port or (443 if parsed.scheme == "https" else 80),
    )


class SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects that could forward a credential to another origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if _origin(req.full_url) != _origin(newurl):
            raise urllib.error.URLError("cross-origin redirect rejected")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_same_origin(request, *, timeout=3):
    return urllib.request.build_opener(SameOriginRedirectHandler()).open(
        request, timeout=timeout
    )


def _configured_loopback_ports() -> frozenset[int]:
    ports = {int(MERIDIAN_DEFAULT_PORT), int(OLLAMA_LOCAL_DEFAULT_PORT), 8000}
    for variable in (MERIDIAN_PORT_ENV, OMLX_PORT_ENV, OLLAMA_LOCAL_PORT_ENV):
        value = os.environ.get(variable, "").strip()
        if value.isdigit() and 1 <= int(value) <= 65535:
            ports.add(int(value))
    ollama_host = os.environ.get(OLLAMA_HOST_ENV, "").strip()
    if ollama_host:
        try:
            ollama_port = urlsplit(ollama_host).port
        except ValueError:
            ollama_port = None
        if ollama_port is not None:
            ports.add(ollama_port)
    return frozenset(ports)


def _configured_litellm_port() -> int | None:
    """Return the statically configured LiteLLM port, independent of service state."""
    value = os.environ.get("LITELLM_PORT", "4000").strip()
    if not value.isdigit():
        return None
    port = int(value)
    return port if 1 <= port <= 65535 else None


def _configured_override_origins() -> tuple[frozenset[str], frozenset[tuple[str, int]]]:
    """Return trusted origins supplied through repository URL contracts."""
    https_hosts = set()
    loopback_origins = set()
    override_names = set(PROVIDER_BASE_URL_ENVS.values()) | {
        OMLX_BASE_URL_ENV,
        OLLAMA_HOST_ENV,
    }
    for variable in override_names:
        value = os.environ.get(variable, "").strip()
        if not value:
            continue
        try:
            parsed = urlsplit(value)
            hostname = parsed.hostname
            port = parsed.port
        except ValueError:
            continue
        if not hostname or parsed.username or parsed.password:
            continue
        if parsed.scheme == "https" and port in (None, 443):
            https_hosts.add(hostname)
        elif (
            parsed.scheme == "http"
            and hostname in LOOPBACK_HOSTS
            and port is not None
            and 1 <= port <= 65535
        ):
            loopback_origins.add((hostname, port))
    return frozenset(https_hosts), frozenset(loopback_origins)


def _validate_catalogue_url(url: str, *, strict: bool = False) -> None:
    parsed = urlsplit(url)
    hostname = parsed.hostname
    port = parsed.port
    credentials = parsed.username or parsed.password
    override_https_hosts, override_loopback_origins = _configured_override_origins()
    litellm_port = _configured_litellm_port()
    strict_valid = (
        parsed.scheme == "https"
        and hostname == "opencode.ai"
        and not credentials
        and port in (None, 443)
    )
    audited_cloud_valid = (
        parsed.scheme == "https"
        and hostname in TRUSTED_HTTPS_HOSTS | override_https_hosts
        and not credentials
        and port in (None, 443)
    )
    loopback_valid = (
        parsed.scheme == "http"
        and hostname in LOOPBACK_HOSTS
        and not credentials
        and (
            port in _configured_loopback_ports()
            or (litellm_port is not None and port == litellm_port)
            or (hostname, port) in override_loopback_origins
        )
    )
    if not (strict_valid if strict else audited_cloud_valid or loopback_valid):
        raise ValueError(f"Unsupported model catalogue URL: {url!r}")


def get_catalogue(url: str, api_key: str = "", *, strict: bool = False):
    """Fetch a raw OpenAI-compatible catalogue, or None when unavailable."""
    _validate_catalogue_url(url, strict=strict)
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    request = urllib.request.Request(url, headers=headers, method="GET")
    # URL scheme, host, and local port are validated against fixed allowlists.
    with open_same_origin(request, timeout=3) as response:
        return json.loads(response.read().decode("utf-8"))


def get_models(url: str, api_key: str = "", stats: dict[str, int] | None = None):
    """Fetch model IDs, returning None for unavailable/authenticated endpoints."""
    if stats is not None:
        stats["checked"] += 1
    try:
        data = get_catalogue(url, api_key)
        return {str(item.get("id")) for item in data.get("data", []) if item.get("id")}
    except urllib.error.HTTPError as exc:
        if stats is not None:
            stats["checked"] -= 1
            stats["skipped"] += 1
        logger.warning(
            "Could not authenticate to %s (HTTP %s) — skipping", url, exc.code
        )
        return None
    except Exception as exc:
        if stats is not None:
            stats["checked"] -= 1
            stats["skipped"] += 1
        logger.warning("Could not reach %s — skipping (%s)", url, exc)
        return None


def endpoint_models_url(base_url: str) -> str:
    base_url = base_url.rstrip("/")
    for suffix in ("/chat/completions", "/responses"):
        if base_url.endswith(suffix):
            base_url = base_url[: -len(suffix)]
            break
    if not base_url.endswith("/v1"):
        base_url += "/v1"
    return base_url + "/models"


def load_allowlists(paths: dict[str, Path]) -> dict[str, set[str]]:
    result = {}
    for provider, path in paths.items():
        if not path.exists():
            logger.warning("Missing %s model allowlist — skipping", path)
            continue
        try:
            models = json.loads(path.read_text(encoding="utf-8")).get("models", {})
            # Display names are not requestable provider model IDs.
            result[provider] = set(models) if isinstance(models, dict) else set()
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read %s — skipping (%s)", path, exc)
    return result
