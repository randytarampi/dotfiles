from datetime import datetime, timedelta, timezone

import pytest

from free_gateway_evidence import EvidenceError, validate_free_gateway_evidence
from free_route_manifest import build_manifest

NOW = datetime(2026, 10, 30, tzinfo=timezone.utc)


def fixture():
    entries = [
        {
            "model_name": "zen",
            "litellm_params": {
                "model": "cohere/command-r",
                "api_base": "https://api.cohere.com/v2",
            },
            "_route_provenance": {
                "upstream_host": "cohere",
                "upstream_model_id": "cohere/command-r",
                "metadata_model_id": "command-r",
                "source_kind": "live-catalogue",
                "metadata_provider": "cohere",
                "observed_at": (NOW - timedelta(minutes=1)).isoformat(),
                "expires_at": (NOW + timedelta(hours=1)).isoformat(),
            },
        }
    ]
    observed = NOW - timedelta(minutes=1)
    expires = NOW + timedelta(hours=1)
    manifest = build_manifest(entries, now=NOW)
    md = {
        "observed_at": observed.isoformat(),
        "expires_at": expires.isoformat(),
        "source": "models.dev",
        "models": {
            "cohere": {
                "id": "cohere",
                "models": {"command-r": {"reasoning": True, "tool_call": True}},
            }
        },
    }
    gateway = {
        "observed_at": observed.isoformat(),
        "expires_at": expires.isoformat(),
        "source": "gateway /v1/models",
        "models": ["zen"],
    }
    entries[0]["_route_provenance"]["metadata_provider"] = "cohere"
    return manifest, entries, md, gateway


def openrouter_fixture():
    manifest, entries, md, gateway = fixture()
    entry = {
        "model_name": "foreign-free",
        "litellm_params": {
            "model": "openrouter/vendor/model:free",
            "api_base": "https://openrouter.ai/api/v1",
        },
        "_route_provenance": {
            "upstream_host": "openrouter",
            "upstream_model_id": "vendor/model:free",
            "metadata_model_id": "vendor/model",
            "source_kind": "live-catalogue",
            "metadata_provider": "vendor",
            "observed_at": (NOW - timedelta(minutes=1)).isoformat(),
            "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        },
    }
    entries.append(entry)
    entries[0]["_route_provenance"]["metadata_provider"] = "cohere"
    entry["_route_provenance"]["metadata_provider"] = "vendor"
    manifest = build_manifest(entries, now=NOW)
    md["models"]["vendor"] = {
        "id": "vendor",
        "models": {"model": {"reasoning": False}},
    }
    gateway["models"] = ["zen", "foreign-free"]
    entries[1]["_route_provenance"]["metadata_provider"] = "vendor"
    return manifest, entries, md, gateway


def normalize(bundle):
    manifest, entries, md, gateway = bundle
    return validate_free_gateway_evidence(
        manifest=manifest,
        expected_config_routes=entries,
        gateway_models=gateway,
        trusted_endpoints={
            "https://api.cohere.com/v2": "cohere",
            "https://openrouter.ai/api/v1": "openrouter",
        },
        models_dev=md,
        now=NOW,
    )


def test_fresh_bound_cohere_gateway_alias_is_candidate_with_metadata():
    candidates = normalize(fixture())
    assert len(candidates) == 1
    assert candidates[0].serving_ref == "zen"
    assert candidates[0].upstream_model == "cohere/command-r"
    assert candidates[0].capabilities == {"reasoning": True, "tool_call": True}


def test_openrouter_free_model_is_free_classified_but_not_zen_candidate():
    bundle = openrouter_fixture()
    bundle[3]["models"] = ["zen"]
    candidates = normalize(bundle)
    assert [candidate.serving_ref for candidate in candidates] == ["zen"]
    assert bundle[0]["routes"][1]["classification"] == "free-classified"


def test_route_projection_and_per_route_freshness_are_bound():
    bundle = fixture()
    changed = [
        dict(
            bundle[1][0],
            litellm_params={**bundle[1][0]["litellm_params"], "model": "cohere/other"},
        )
    ]
    with pytest.raises(EvidenceError, match="digest"):
        validate_free_gateway_evidence(
            manifest=bundle[0],
            expected_config_routes=changed,
            gateway_models=bundle[3],
            trusted_endpoints={"https://api.cohere.com/v2": "cohere"},
            models_dev=bundle[2],
            now=NOW,
        )
    bundle[0]["routes"][0]["expires_at"] = (NOW + timedelta(minutes=1)).isoformat()
    bundle[1][0]["_route_provenance"]["expires_at"] = (
        NOW + timedelta(minutes=1)
    ).isoformat()
    candidates = normalize(bundle)
    assert candidates[0].expires_at == NOW + timedelta(minutes=1)
    bundle[0]["routes"][0]["expires_at"] = (NOW - timedelta(seconds=1)).isoformat()
    bundle[1][0]["_route_provenance"]["expires_at"] = (
        NOW - timedelta(seconds=1)
    ).isoformat()
    assert normalize(bundle) == []


def test_alias_with_paid_backend_is_excluded_as_a_whole():
    bundle = fixture()
    paid = dict(bundle[1][0])
    paid["litellm_params"] = {
        **paid["litellm_params"],
        "model": "vendor/paid",
        "api_base": "https://openrouter.ai/api/v1",
    }
    paid["_route_provenance"] = {
        **paid["_route_provenance"],
        "upstream_host": "openrouter",
        "upstream_model_id": "vendor/paid",
        "metadata_model_id": "paid",
        "metadata_provider": "vendor",
    }
    bundle[1].append(paid)
    bundle[1][0]["_route_provenance"]["metadata_provider"] = "cohere"
    bundle[1][1]["model_name"] = "zen"
    bundle[2]["models"]["vendor"] = {"id": "vendor", "models": {"paid": {}}}
    bundle[3]["models"] = ["zen"]
    bundle = (
        build_manifest(bundle[1], now=NOW),
        bundle[1],
        bundle[2],
        bundle[3],
    )
    assert normalize(bundle) == []


def test_unbound_expected_routes_and_digest_tampering_are_rejected():
    bundle = fixture()
    with pytest.raises(EvidenceError, match="binding"):
        validate_free_gateway_evidence(
            manifest=bundle[0],
            expected_config_routes=None,
            gateway_models=bundle[3],
            trusted_endpoints={},
            models_dev=bundle[2],
            now=NOW,
        )
    bundle[0]["routes"][0]["wire_model"] = "attacker/model"
    with pytest.raises(EvidenceError, match="digest|projection"):
        normalize(bundle)


def test_unadvertised_manifest_alias_is_not_selected_and_stale_metadata_is_rejected():
    bundle = fixture()
    bundle[3]["models"] = []
    assert normalize(bundle) == []
    bundle = fixture()
    bundle[2]["expires_at"] = (NOW - timedelta(seconds=1)).isoformat()
    with pytest.raises(EvidenceError, match="stale"):
        normalize(bundle)


def test_unknown_native_backend_alias_is_excluded():
    bundle = fixture()
    params = bundle[1][0]["litellm_params"]
    params.pop("api_base")
    bundle[1][0]["_route_provenance"]["upstream_host"] = "unknown"
    bundle[1][0]["_route_provenance"].update(
        observed_at=(NOW - timedelta(minutes=1)).isoformat(),
        expires_at=(NOW + timedelta(hours=1)).isoformat(),
    )
    manifest = build_manifest(bundle[1], now=NOW)
    bundle = (manifest, bundle[1], bundle[2], bundle[3])
    assert normalize(bundle) == []


def test_native_backend_config_binding_tampering_still_fails_closed():
    bundle = fixture()
    bundle[1][0]["litellm_params"].pop("api_base")
    bundle[1][0]["_route_provenance"]["upstream_host"] = "unknown"
    with pytest.raises(EvidenceError, match="digest"):
        normalize(bundle)
