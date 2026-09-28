"""Shared authenticated model-catalogue accessors."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import logger


def get_catalogue(url: str, api_key: str = ""):
    """Fetch a raw OpenAI-compatible catalogue, or None when unavailable."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=3) as response:
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
            values = set(models) if isinstance(models, dict) else set()
            if isinstance(models, dict):
                values.update(
                    item.get("name")
                    for item in models.values()
                    if isinstance(item, dict) and item.get("name")
                )
            result[provider] = values
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read %s — skipping (%s)", path, exc)
    return result
