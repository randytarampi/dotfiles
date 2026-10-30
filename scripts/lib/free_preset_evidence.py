"""Normalize caller-supplied catalogue snapshots into free-preset candidates.

This module performs no discovery or I/O. Callers must supply fresh snapshots
and explicit bindings for route origins.
"""

from collections import Counter
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

from free_presets import Candidate


class EvidenceError(ValueError):
    """Raised when supplied evidence is incomplete or ambiguous."""


def _timestamp(value, label):
    if not isinstance(value, str):
        raise EvidenceError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EvidenceError(f"{label} must be timezone-aware")
    return parsed


def _envelope(snapshot, now):
    if not isinstance(snapshot, dict):
        raise EvidenceError("snapshot must be an object")
    observed = _timestamp(snapshot.get("observed_at"), "observed_at")
    expires = _timestamp(snapshot.get("expires_at"), "expires_at")
    source = snapshot.get("source")
    if not isinstance(source, str) or not source.strip():
        raise EvidenceError("snapshot source is required")
    if observed > now or expires <= now or expires <= observed:
        raise EvidenceError("snapshot is stale or has an invalid freshness interval")
    return observed, expires, source


def _capabilities(model):
    caps = {}
    reasoning = model.get("reasoning")
    tool_call = model.get("tool_call")
    if isinstance(reasoning, bool):
        caps["reasoning"] = reasoning
    if isinstance(tool_call, bool):
        caps["tool_call"] = tool_call
    modalities = model.get("modalities")
    if isinstance(modalities, dict):
        inputs = modalities.get("input")
        outputs = modalities.get("output")
        if isinstance(inputs, list):
            caps["image_input"] = "image" in inputs
        if isinstance(outputs, list):
            caps["text_output"] = "text" in outputs
    return caps


def _canonical_origin(value):
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            return None
        port = parsed.port
    except (TypeError, ValueError):
        return None
    host = parsed.hostname.lower()
    default_port = (parsed.scheme == "http" and port == 80) or (
        parsed.scheme == "https" and port == 443
    )
    netloc = host if port is None or default_port else f"{host}:{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), netloc, path, "", ""))


def normalize_free_preset_evidence(
    *, models_dev, direct_routes, gateway, trusted_endpoints, now, trusted_native=None
):
    """Return Candidate records from three explicit, fresh catalogue snapshots.

    ``direct_routes`` entries bind a serving_ref to an exact catalogue provider
    and model ID. ``gateway`` contains its model_list snapshot, an alias-to-
    backend inventory, and optional native-wire provenance by alias.
    ``trusted_endpoints`` maps canonical API origins to upstream host names.
    """
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise EvidenceError("now must be timezone-aware")
    if not isinstance(models_dev, dict) or not isinstance(
        models_dev.get("models"), dict
    ):
        raise EvidenceError("models.dev snapshot must contain provider model mappings")
    md_observed, md_expires, md_source = _envelope(models_dev, now)
    if not isinstance(trusted_endpoints, dict):
        raise EvidenceError("caller-trusted endpoint map is required")
    trusted_native = {} if trusted_native is None else trusted_native
    if not isinstance(trusted_native, dict):
        raise EvidenceError("caller-trusted native bindings must be a mapping")
    windows = [(md_observed, md_expires)]
    sources = [md_source]

    catalogue = {}
    for provider_id, provider_record in models_dev["models"].items():
        if not isinstance(provider_id, str) or not isinstance(provider_record, dict):
            raise EvidenceError("models.dev provider mapping is malformed")
        records = provider_record.get("models")
        if not isinstance(records, dict):
            raise EvidenceError("models.dev models must be keyed by exact model ID")
        for model_id, data in records.items():
            if not isinstance(model_id, str) or not isinstance(data, dict):
                raise EvidenceError("models.dev model record is malformed")
            catalogue[(provider_id, model_id)] = _capabilities(data)

    candidates = []
    if direct_routes is not None:
        direct_observed, direct_expires, direct_source = _envelope(direct_routes, now)
        windows.append((direct_observed, direct_expires))
        sources.append(direct_source)
        routes = direct_routes.get("routes")
        if not isinstance(routes, list):
            raise EvidenceError("direct routes must be a list")
        direct_window = (direct_observed, direct_expires)
    else:
        routes = []
        direct_window = None
    for binding in routes:
        if not isinstance(binding, dict):
            raise EvidenceError("direct route binding is malformed")
        model_id, provider, ref = (
            binding.get("model_id"),
            binding.get("provider_id"),
            binding.get("serving_ref"),
        )
        api_base = binding.get("api_base")
        native_host = trusted_native.get((ref, model_id))
        if not all(isinstance(v, str) and v.strip() for v in (model_id, provider, ref)):
            raise EvidenceError(
                "direct route binding requires explicit provider, model, and ref"
            )
        if api_base is not None:
            origin = _canonical_origin(api_base)
            host = trusted_endpoints.get(origin) if origin else None
            if not isinstance(host, str) or not host.strip():
                raise EvidenceError("direct route origin is not caller-trusted")
        elif isinstance(native_host, str) and native_host.strip():
            host = native_host
        else:
            raise EvidenceError(
                "direct route requires trusted origin or native binding"
            )
        upstream_model = binding.get("upstream_model", model_id)
        metadata_model_id = binding.get("metadata_model_id", model_id)
        metadata_provider = binding.get("metadata_provider", provider)
        if not all(
            isinstance(v, str) and v.strip()
            for v in (upstream_model, metadata_model_id, metadata_provider)
        ):
            raise EvidenceError("direct upstream identity binding is malformed")
        caps = catalogue.get((metadata_provider, metadata_model_id), {})
        observed, expires = md_observed, md_expires
        if direct_window is not None:
            observed = max(observed, direct_window[0])
            expires = min(expires, direct_window[1])
        candidates.append(
            Candidate(
                ref,
                host,
                upstream_model,
                "; ".join(sources),
                observed,
                expires,
                caps,
                True,
            )
        )

    gateway_window = None
    if gateway is not None:
        if not isinstance(gateway, dict):
            raise EvidenceError("gateway snapshot configuration is malformed")
        gateway_snapshot = gateway.get("model_list_snapshot")
        gw_observed, gw_expires, gw_source = _envelope(gateway_snapshot, now)
        gateway_window = (gw_observed, gw_expires)
        windows.append((gw_observed, gw_expires))
        sources.append(gw_source)
        model_list = gateway_snapshot.get("model_list")
        alias_backends = gateway.get("alias_backends")
        native = gateway.get("native_provenance", {})
        if (
            not isinstance(model_list, list)
            or not isinstance(alias_backends, dict)
            or not isinstance(native, dict)
        ):
            raise EvidenceError(
                "gateway model_list, alias inventory, or native bindings are malformed"
            )
    else:
        model_list, alias_backends, native = [], {}, {}
    entries = {}
    for entry in model_list:
        if not isinstance(entry, dict) or not isinstance(entry.get("model_name"), str):
            raise EvidenceError("gateway model_list entry is malformed")
        params = entry.get("litellm_params")
        if not isinstance(params, dict) or not isinstance(params.get("model"), str):
            raise EvidenceError("gateway route lacks an upstream model")
        entries.setdefault(entry["model_name"], []).append(params)
    for alias, backends in alias_backends.items():
        if not isinstance(alias, str) or not isinstance(backends, list) or not backends:
            raise EvidenceError("gateway alias inventory is malformed")
        configured = entries.get(alias, [])

        def route_key(record):
            return record.get("model"), (
                _canonical_origin(record.get("api_base"))
                if record.get("api_base") is not None
                else None
            )

        configured_keys = Counter(
            (
                p.get("model"),
                (
                    _canonical_origin(p.get("api_base"))
                    if p.get("api_base") is not None
                    else None
                ),
            )
            for p in configured
        )
        inventory_keys = Counter(route_key(b) for b in backends if isinstance(b, dict))
        if len(inventory_keys) != len(backends) or configured_keys != inventory_keys:
            raise EvidenceError(
                "gateway backend inventory mismatch or duplicate binding"
            )
        for backend in backends:
            if not isinstance(backend, dict):
                raise EvidenceError("gateway backend record is malformed")
            upstream_model = backend.get("model")
            api_base = backend.get("api_base")
            params = [
                p
                for p in configured
                if p.get("model") == upstream_model
                and (
                    _canonical_origin(p.get("api_base"))
                    if p.get("api_base") is not None
                    else None
                )
                == (_canonical_origin(api_base) if api_base is not None else None)
            ]
            if (
                len(params) != 1
                or len([b for b in backends if route_key(b) == route_key(backend)]) != 1
            ):
                raise EvidenceError(
                    f"gateway backend binding is ambiguous for {alias!r}"
                )
            if api_base is not None:
                origin = _canonical_origin(api_base)
                host = trusted_endpoints.get(origin) if origin else None
                if not isinstance(host, str) or not host.strip():
                    raise EvidenceError(f"untrusted gateway endpoint for {alias!r}")
            else:
                provenance = native.get(alias)
                if (
                    not isinstance(provenance, dict)
                    or provenance.get("model") != upstream_model
                ):
                    raise EvidenceError(
                        f"native gateway provenance missing for {alias!r}"
                    )
                host = provenance.get("upstream_host")
                if not isinstance(host, str) or not host.strip():
                    raise EvidenceError(
                        f"native gateway provenance invalid for {alias!r}"
                    )
            binding = next(
                (
                    p
                    for p in configured
                    if p.get("model") == upstream_model
                    and (
                        _canonical_origin(p.get("api_base"))
                        if p.get("api_base") is not None
                        else None
                    )
                    == (_canonical_origin(api_base) if api_base is not None else None)
                ),
                None,
            )
            identity = backend.get("upstream_identity") or (
                binding.get("upstream_identity") if binding else None
            )
            if not isinstance(identity, dict):
                raise EvidenceError(
                    "gateway backend requires explicit upstream identity binding"
                )
            identity_wire = identity.get("wire_model")
            if identity_wire != upstream_model:
                raise EvidenceError(
                    "gateway upstream identity does not match wire model"
                )
            upstream_host, upstream_model = identity.get("upstream_host"), identity.get(
                "upstream_model"
            )
            metadata_provider, metadata_model = identity.get(
                "metadata_provider"
            ), identity.get("metadata_model_id")
            if not all(
                isinstance(v, str) and v.strip()
                for v in (
                    upstream_host,
                    upstream_model,
                    metadata_provider,
                    metadata_model,
                )
            ):
                raise EvidenceError("gateway upstream identity is incomplete")
            if api_base is not None:
                origin = _canonical_origin(api_base)
                if trusted_endpoints.get(origin) != upstream_host:
                    raise EvidenceError(
                        "gateway upstream identity conflicts with trusted endpoint"
                    )
            elif (
                trusted_native.get((alias, identity_wire)) != upstream_host
                or host != upstream_host
            ):
                raise EvidenceError(
                    "gateway upstream identity conflicts with native binding"
                )
            caps = catalogue.get((metadata_provider, metadata_model), {})
            observed = max(md_observed, gateway_window[0])
            expires = min(md_expires, gateway_window[1])
            candidates.append(
                Candidate(
                    alias,
                    upstream_host,
                    upstream_model,
                    "; ".join(sources),
                    observed,
                    expires,
                    caps,
                    True,
                )
            )
    observed, expires = max(item[0] for item in windows), min(
        item[1] for item in windows
    )
    if expires <= now or expires <= observed:
        raise EvidenceError("combined evidence freshness interval is empty")
    return [
        Candidate(
            c.serving_ref,
            c.upstream_host,
            c.upstream_model,
            c.provenance,
            observed,
            expires,
            c.capabilities,
            c.source_binding_trusted,
        )
        for c in candidates
    ]
