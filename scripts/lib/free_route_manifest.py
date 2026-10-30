"""Credential-free provenance manifest for generated LiteLLM routes."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import litellm_config

SCHEMA_VERSION = 3
_TRUSTED_SOURCES = {"live-catalogue"}


def _canonical_api_base(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            return None
        host = parsed.hostname.lower()
        if parsed.port:
            host += f":{parsed.port}"
        return urlunsplit(
            (parsed.scheme.lower(), host, parsed.path.rstrip("/"), "", "")
        )
    except ValueError:
        return None


def _route_projection(entries):
    routes = []
    for entry in entries:
        params = entry.get("litellm_params") or {}
        if not isinstance(params, dict):
            continue
        routes.append(
            {
                "alias": str(entry.get("model_name", "")),
                "wire_model": str(params.get("model", "")),
                "api_base": _canonical_api_base(params.get("api_base")),
            }
        )
    return sorted(
        routes,
        key=lambda route: (
            route["alias"],
            route["wire_model"],
            route["api_base"] or "",
        ),
    )


def expected_config_routes(entries):
    """Project trusted config routes without credentials or unrelated settings."""
    projected = []
    for entry in entries:
        params = entry.get("litellm_params") or {}
        row = {"model_name": entry.get("model_name"), "litellm_params": {}}
        for key in ("model", "api_base"):
            if key in params:
                row["litellm_params"][key] = params[key]
        provenance = entry.get("_route_provenance")
        if isinstance(provenance, dict):
            row["_route_provenance"] = {
                key: provenance[key]
                for key in (
                    "upstream_host",
                    "upstream_model_id",
                    "metadata_model_id",
                    "metadata_provider",
                    "source_kind",
                    "observed_at",
                    "expires_at",
                )
                if key in provenance
            }
        projected.append(row)
    return projected


def config_binding_digest(entries):
    encoded = json.dumps(
        _route_projection(entries), sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _manifest_projection(entries):
    rows = []
    for entry in entries:
        params = entry.get("litellm_params") or {}
        if not isinstance(params, dict):
            continue
        provenance = entry.get("_route_provenance") or {}
        rows.append(
            {
                "alias": str(entry.get("model_name", "")),
                "wire_model": str(params.get("model", "")),
                "api_base": _canonical_api_base(params.get("api_base")),
                "upstream_host": str(provenance.get("upstream_host") or "unknown"),
                "upstream_model_id": str(
                    provenance.get("upstream_model_id") or "unknown"
                ),
                "metadata_model_id": str(
                    provenance.get("metadata_model_id")
                    or provenance.get("upstream_model_id")
                    or "unknown"
                ),
                "metadata_provider": str(
                    provenance.get("metadata_provider") or "unknown"
                ),
                "source_kind": str(provenance.get("source_kind") or "unknown"),
            }
        )
    return sorted(
        rows,
        key=lambda row: tuple(
            row[key] or ""
            for key in (
                "alias",
                "wire_model",
                "api_base",
                "upstream_host",
                "upstream_model_id",
                "metadata_provider",
                "metadata_model_id",
                "source_kind",
            )
        ),
    )


def binding_digest(entries):
    encoded = json.dumps(
        _manifest_projection(entries), sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _parse_time(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo is not None else None
    except ValueError:
        return None


def build_manifest(entries, *, observed_at=None, expires_at=None, now=None):
    now = now or datetime.now(timezone.utc)
    routes = []
    for route in _manifest_projection(entries):
        source = next(
            (entry.get("_route_provenance") or {})
            for entry in entries
            if entry.get("model_name") == route["alias"]
            and (entry.get("litellm_params") or {}).get("model") == route["wire_model"]
            and _canonical_api_base((entry.get("litellm_params") or {}).get("api_base"))
            == route["api_base"]
        )
        observed_value = source.get("observed_at")
        if observed_value is None:
            observed_value = observed_at
        expiry_value = source.get("expires_at")
        if expiry_value is None:
            expiry_value = expires_at
        observed, expiry = _parse_time(observed_value), _parse_time(expiry_value)
        fresh = (
            route["source_kind"] in _TRUSTED_SOURCES
            and observed is not None
            and expiry is not None
            and observed <= now < expiry
            and route["upstream_host"] != "unknown"
            and route["upstream_model_id"] != "unknown"
            and route["metadata_provider"] != "unknown"
            and route["metadata_model_id"] != "unknown"
        )
        result = litellm_config.classify_free_route(
            route["upstream_model_id"],
            route["upstream_host"],
            route["source_kind"],
            "gateway-route",
        )
        if not fresh:
            result["classification"] = "UNKNOWN"
            result["classification_reason"] = (
                "route provenance is not fresh, recognized catalogue evidence"
            )
        routes.append(
            {
                **route,
                "evidence_state": "VERIFIED" if fresh else "UNKNOWN",
                "observed_at": observed_value if fresh else None,
                "expires_at": expiry_value if fresh else None,
                "classification": result["classification"],
                "classification_reason": result["classification_reason"],
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "config_binding_sha256": config_binding_digest(entries),
        "provenance_binding_sha256": binding_digest(entries),
        "routes": routes,
    }


def _check_path_chain(path):
    absolute = path.absolute()
    for parent in reversed(absolute.parents):
        if parent.exists() and parent.is_symlink():
            raise ValueError(f"refusing symlink path component: {parent}")


def write_manifest(path, manifest):
    path = Path(path)
    _check_path_chain(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _check_path_chain(path)
    if path.is_symlink():
        raise ValueError(f"refusing symlink manifest path: {path}")
    if path.exists():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError(f"refusing unsafe manifest target: {path}")
    fd, temp_name = tempfile.mkstemp(prefix=".free-route-manifest.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, sort_keys=True, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
