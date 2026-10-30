"""Validate supplied LiteLLM manifest and fresh gateway evidence without I/O."""

from collections import Counter
from datetime import datetime

import free_route_manifest
from free_preset_evidence import EvidenceError, normalize_free_preset_evidence


def validate_free_gateway_evidence(
    *,
    manifest,
    expected_config_routes,
    gateway_models,
    trusted_endpoints,
    models_dev,
    now,
    trusted_native=None,
):
    """Return candidates only when manifest/config/gateway evidence is bound.

    ``expected_config_routes`` must come from the trusted config consumer, not
    from the manifest. ``gateway_models`` is a fresh /v1/models envelope whose
    ``models`` list advertises aliases; it does not attest backend identities.
    """
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise EvidenceError("now must be timezone-aware")
    if not isinstance(expected_config_routes, list):
        raise EvidenceError(
            "manifest binding requires a trusted expected config route projection"
        )
    if any(
        not isinstance(row, dict)
        or not isinstance(row.get("model_name"), str)
        or not isinstance(row.get("litellm_params"), dict)
        for row in expected_config_routes
    ):
        raise EvidenceError("trusted config route projection is malformed")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("routes"), list):
        raise EvidenceError("route manifest is malformed")
    routes = manifest["routes"]
    if manifest.get("schema_version") != free_route_manifest.SCHEMA_VERSION:
        raise EvidenceError("unsupported route manifest schema")
    if manifest.get(
        "config_binding_sha256"
    ) != free_route_manifest.config_binding_digest(expected_config_routes):
        raise EvidenceError("manifest config binding digest mismatch")

    expected_projection = Counter(
        (row["alias"], row["wire_model"], row["api_base"])
        for row in free_route_manifest._route_projection(expected_config_routes)
    )
    manifest_projection = Counter(
        (route.get("alias"), route.get("wire_model"), route.get("api_base"))
        for route in routes
    )
    if expected_projection != manifest_projection:
        raise EvidenceError(
            "manifest config binding digest or route projection mismatch"
        )

    expected_identity = {}
    for entry in expected_config_routes:
        params = entry.get("litellm_params") or {}
        expected_identity[
            (
                entry.get("model_name"),
                params.get("model"),
                free_route_manifest._canonical_api_base(params.get("api_base")),
            )
        ] = (
            entry.get("_route_provenance") or {}
        )

    provenance_entries = []
    for route in routes:
        if not isinstance(route, dict):
            raise EvidenceError("manifest route is malformed")
        if not isinstance(route, dict):
            raise EvidenceError("manifest route is malformed")
        provenance_entries.append(
            {
                "model_name": route.get("alias"),
                "litellm_params": {
                    "model": route.get("wire_model"),
                    "api_base": route.get("api_base"),
                },
                "_route_provenance": {
                    "upstream_host": route.get("upstream_host"),
                    "upstream_model_id": route.get("upstream_model_id"),
                    "metadata_model_id": route.get("metadata_model_id"),
                    "metadata_provider": route.get("metadata_provider"),
                    "source_kind": route.get("source_kind"),
                    "observed_at": route.get("observed_at"),
                    "expires_at": route.get("expires_at"),
                },
            }
        )
    binding_routes = [
        {
            "model_name": route.get("alias"),
            "litellm_params": {
                "model": route.get("wire_model"),
                "api_base": route.get("api_base"),
            },
            "_route_provenance": {
                key: route.get(key)
                for key in (
                    "upstream_host",
                    "upstream_model_id",
                    "metadata_model_id",
                    "metadata_provider",
                    "source_kind",
                )
            },
        }
        for route in routes
    ]
    # The manifest digest binds identity, not mutable validity state; timestamps
    # are checked independently against the trusted config envelope below.
    if manifest.get("provenance_binding_sha256") != free_route_manifest.binding_digest(
        provenance_entries
    ):
        raise EvidenceError("manifest provenance binding digest mismatch")
    for route in routes:
        identity = expected_identity.get(
            (route.get("alias"), route.get("wire_model"), route.get("api_base")), {}
        )
        if route.get("evidence_state") == "VERIFIED" and (
            route.get("observed_at") != identity.get("observed_at")
            or route.get("expires_at") != identity.get("expires_at")
        ):
            raise EvidenceError("manifest route freshness differs from trusted config")

    if not isinstance(gateway_models, dict):
        raise EvidenceError("gateway model snapshot is malformed")
    free_preset_evidence_timestamp(gateway_models, now)
    aliases = gateway_models.get("models")
    if not isinstance(aliases, list) or any(
        not isinstance(alias, str) for alias in aliases
    ):
        raise EvidenceError("gateway advertised aliases are malformed")
    if len(set(aliases)) != len(aliases):
        raise EvidenceError("gateway advertised aliases contain duplicates")

    routes_by_alias = {}
    for route in routes:
        routes_by_alias.setdefault(route.get("alias"), []).append(route)
    advertised = set(aliases)
    manifest_aliases = set(routes_by_alias)
    # /v1/models may omit aliases hidden by credentials; absent aliases are
    # simply unavailable. An advertised alias absent from the manifest is
    # unknown and fails closed.

    # Each alias is atomic: an unknown, stale, or non-free backend disqualifies
    # the entire alias instead of allowing a paid backend to be hidden.
    selected = []
    for alias in advertised & manifest_aliases:
        members = routes_by_alias[alias]
        valid = True
        for route in members:
            if not route.get("api_base") and (
                not isinstance(trusted_native, dict)
                or trusted_native.get((alias, route.get("wire_model")))
                != route.get("upstream_host")
            ):
                # An unsupported native alias is unqualified, not evidence corruption.
                valid = False
                break
            start = free_preset_evidence_timestamp_value(route.get("observed_at"))
            end = free_preset_evidence_timestamp_value(route.get("expires_at"))
            if (
                route.get("evidence_state") != "VERIFIED"
                or start is None
                or end is None
                or start > now
                or end <= start
                or route.get("source_kind") != "live-catalogue"
                or end <= now
            ):
                valid = False
                break
            classification = free_route_manifest_litellm().classify_free_route(
                route.get("upstream_model_id"),
                route.get("upstream_host"),
                route.get("source_kind"),
                "gateway-route",
            )
            if classification["classification"] != "free-classified":
                valid = False
                break
        if valid:
            selected.extend(members)
    if not selected:
        return []

    model_list = []
    inventory = {}
    native = {}
    for route in selected:
        bound_identity = expected_identity.get(
            (route["alias"], route["wire_model"], route.get("api_base")), {}
        )
        identity = {
            "wire_model": route["wire_model"],
            "upstream_host": route["upstream_host"],
            "upstream_model": route["upstream_model_id"],
            "metadata_provider": bound_identity.get("metadata_provider"),
            "metadata_model_id": route.get("metadata_model_id"),
        }
        if not identity["metadata_provider"] or not identity["metadata_model_id"]:
            raise EvidenceError("manifest lacks exact metadata provider/model identity")
        if (
            bound_identity.get("upstream_host") != identity["upstream_host"]
            or bound_identity.get("upstream_model_id") != identity["upstream_model"]
            or bound_identity.get("metadata_model_id") != identity["metadata_model_id"]
        ):
            raise EvidenceError(
                "manifest upstream identity conflicts with trusted route provenance"
            )
        params = {"model": route["wire_model"], "upstream_identity": identity}
        if route.get("api_base"):
            params["api_base"] = route["api_base"]
        else:
            native[route["alias"]] = {
                "model": route["wire_model"],
                "upstream_host": route["upstream_host"],
            }
        model_list.append({"model_name": route["alias"], "litellm_params": params})
        inventory.setdefault(route["alias"], []).append(
            {
                "model": route["wire_model"],
                "api_base": route.get("api_base"),
                "upstream_identity": identity,
            }
        )
    route_windows = {}
    for route in selected:
        observed_at = free_preset_evidence_timestamp_value(route["observed_at"])
        expires_at = free_preset_evidence_timestamp_value(route["expires_at"])
        current = route_windows.get(route["alias"])
        route_windows[route["alias"]] = (
            max(observed_at, current[0]) if current else observed_at,
            min(expires_at, current[1]) if current else expires_at,
        )
    # Normalize separately per alias so each candidate retains its conservative
    # route freshness interval rather than inheriting gateway envelope dates.
    candidates = []
    for alias in sorted(route_windows):
        alias_routes = [item for item in model_list if item["model_name"] == alias]
        alias_inventory = {alias: inventory[alias]}
        alias_native = {alias: native[alias]} if alias in native else {}
        route_observed, route_expires = route_windows[alias]
        if route_expires <= route_observed:
            continue
        if route_expires <= now:
            continue
        gateway_observed = free_preset_evidence_timestamp_value(
            gateway_models["observed_at"]
        )
        gateway_expires = free_preset_evidence_timestamp_value(
            gateway_models["expires_at"]
        )
        gateway_snapshot = {
            **gateway_models,
            "observed_at": max(
                route_observed,
                gateway_observed,
            ).isoformat(),
            "expires_at": min(
                route_expires,
                gateway_expires,
            ).isoformat(),
            "model_list": alias_routes,
        }
        candidates.extend(
            normalize_free_preset_evidence(
                models_dev=models_dev,
                direct_routes=None,
                gateway={
                    "model_list_snapshot": gateway_snapshot,
                    "alias_backends": alias_inventory,
                    "native_provenance": alias_native,
                },
                trusted_endpoints=trusted_endpoints,
                trusted_native=trusted_native,
                now=now,
            )
        )
    return candidates


def free_preset_evidence_timestamp(envelope, now):
    from free_preset_evidence import _envelope

    return _envelope(envelope, now)


def free_preset_evidence_timestamp_value(value):
    from free_preset_evidence import _timestamp

    try:
        return _timestamp(value, "route freshness")
    except EvidenceError:
        return None


def free_route_manifest_litellm():
    import litellm_config

    return litellm_config
