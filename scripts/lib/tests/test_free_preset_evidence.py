from datetime import datetime, timedelta, timezone

import pytest

from free_preset_evidence import EvidenceError, normalize_free_preset_evidence

NOW = datetime(2026, 10, 30, tzinfo=timezone.utc)


def stamp(value):
    return value.isoformat()


def snapshots(*, expired=False, future=False):
    observed = NOW + (timedelta(minutes=1) if future else -timedelta(minutes=1))
    expires = NOW - timedelta(seconds=1) if expired else NOW + timedelta(hours=1)
    envelope = {
        "observed_at": stamp(observed),
        "expires_at": stamp(expires),
        "source": "fixture",
    }
    models_dev = {
        **envelope,
        "models": {
            "cohere": {
                "command-r": {
                    "reasoning": True,
                    "tool_call": True,
                    "modalities": {"input": ["text", "image"], "output": ["text"]},
                },
                "paid": {
                    "reasoning": True,
                    "tool_call": True,
                    "modalities": {"input": ["text"], "output": ["text"]},
                },
            }
        },
    }
    # Preserve the actual models.dev provider envelope, not a flattened model map.
    models_dev["models"] = {
        provider: {"id": provider, "models": records}
        for provider, records in models_dev["models"].items()
    }
    direct = {
        **envelope,
        "routes": [
            {
                "serving_ref": "cohere/command-r",
                "model_id": "command-r",
                "provider_id": "cohere",
                "upstream_model": "cohere/command-r",
                "api_base": "https://api.cohere.com/v2",
            }
        ],
    }
    ident = {
        "wire_model": "cohere/command-r",
        "upstream_host": "cohere",
        "upstream_model": "command-r",
        "metadata_provider": "cohere",
        "metadata_model_id": "command-r",
    }
    paid_ident = {
        **ident,
        "wire_model": "cohere/paid",
        "upstream_model": "paid",
        "metadata_model_id": "paid",
    }
    gateway_envelope = {
        **envelope,
        "model_list": [
            {
                "model_name": "alias//exact",
                "litellm_params": {
                    "model": "cohere/command-r",
                    "api_base": "https://api.cohere.com/v2",
                    "upstream_identity": ident,
                },
            },
            {
                "model_name": "alias//exact",
                "litellm_params": {
                    "model": "cohere/paid",
                    "api_base": "https://api.cohere.com/v2",
                    "upstream_identity": paid_ident,
                },
            },
        ],
    }
    gateway = {
        "model_list_snapshot": gateway_envelope,
        "alias_backends": {
            "alias//exact": [
                {
                    "model": "cohere/command-r",
                    "api_base": "https://api.cohere.com/v2",
                    "upstream_identity": ident,
                },
                {
                    "model": "cohere/paid",
                    "api_base": "https://api.cohere.com/v2",
                    "upstream_identity": paid_ident,
                },
            ]
        },
    }
    return models_dev, direct, gateway


def normalize(bundle):
    models, direct, gateway = bundle
    return normalize_free_preset_evidence(
        models_dev=models,
        direct_routes=direct,
        gateway=gateway,
        trusted_endpoints={"https://api.cohere.com/v2": "cohere"},
        now=NOW,
    )


def test_normalizes_direct_and_gateway_preserving_capabilities_and_exact_alias():
    candidates = normalize(snapshots())
    direct, gateway, paid = candidates
    assert direct.capabilities == {
        "reasoning": True,
        "tool_call": True,
        "image_input": True,
        "text_output": True,
    }
    assert gateway.serving_ref == "alias//exact"
    assert gateway.upstream_host == "cohere"
    assert paid.upstream_model == "paid"


def test_unknown_endpoint_fails_without_inventing_host():
    bundle = snapshots()
    bundle[2]["alias_backends"]["alias//exact"][0][
        "api_base"
    ] = "https://unknown.example/v1"
    bundle[2]["model_list_snapshot"]["model_list"][0]["litellm_params"][
        "api_base"
    ] = "https://unknown.example/v1"
    with pytest.raises(EvidenceError, match="untrusted gateway endpoint"):
        normalize(bundle)


def test_gateway_payload_cannot_override_trusted_endpoint_map():
    bundle = snapshots()
    bundle[2]["trusted_endpoints"] = {"https://api.cohere.com/v2": "opencode"}
    assert normalize(bundle)[1].upstream_host == "cohere"


def test_backend_inventory_requires_one_to_one_matching():
    bundle = snapshots()
    bundle[2]["alias_backends"]["alias//exact"] = [
        bundle[2]["alias_backends"]["alias//exact"][0],
        bundle[2]["alias_backends"]["alias//exact"][0],
    ]
    with pytest.raises(EvidenceError, match="inventory|ambiguous"):
        normalize(bundle)


def test_models_dev_actual_provider_mapping_schema():
    bundle = snapshots()
    assert normalize(bundle)[0].capabilities["image_input"] is True


def test_normalized_direct_evidence_resolves_a_complete_free_preset():
    from free_presets import resolve_free_preset

    models, direct, _ = snapshots()
    candidates = normalize_free_preset_evidence(
        models_dev=models,
        direct_routes=direct,
        gateway=None,
        trusted_endpoints={"https://api.cohere.com/v2": "cohere"},
        now=NOW,
    )
    result = resolve_free_preset(candidates, "free", {}, now=NOW)
    assert result.role_choices["observer"].serving_ref == "cohere/command-r"
    assert result.role_choices["oracle"].upstream_model == "cohere/command-r"


def test_payload_native_host_cannot_establish_trust():
    models, direct, _ = snapshots()
    direct["routes"][0].pop("api_base")
    direct["routes"][0]["native_upstream_host"] = "opencode"
    with pytest.raises(EvidenceError, match="native binding"):
        normalize_free_preset_evidence(
            models_dev=models,
            direct_routes=direct,
            gateway=None,
            trusted_endpoints={},
            now=NOW,
        )


def test_direct_route_requires_trusted_origin_not_asserted_host():
    bundle = snapshots()
    bundle[1]["routes"][0]["api_base"] = "https://foreign.example/v1"
    with pytest.raises(EvidenceError, match="direct route origin"):
        normalize(bundle)


def test_optional_route_snapshots_do_not_require_unused_mode():
    bundle = snapshots()
    assert (
        normalize_free_preset_evidence(
            models_dev=bundle[0],
            direct_routes=None,
            gateway=None,
            trusted_endpoints={"https://api.cohere.com/v2": "cohere"},
            now=NOW,
        )
        == []
    )


def test_malformed_route_collection_is_domain_error():
    bundle = snapshots()
    bundle[1]["routes"] = None
    with pytest.raises(EvidenceError, match="routes"):
        normalize(bundle)


@pytest.mark.parametrize("kwargs", [{"expired": True}, {"future": True}])
def test_rejects_stale_or_future_envelopes(kwargs):
    with pytest.raises(EvidenceError, match="stale"):
        normalize(snapshots(**kwargs))


def test_credentials_are_not_copied_into_candidate_or_error():
    bundle = snapshots()
    bundle[2]["model_list_snapshot"]["model_list"][0]["litellm_params"][
        "api_key"
    ] = "do-not-leak"
    candidates = normalize(bundle)
    assert all("do-not-leak" not in repr(candidate) for candidate in candidates)


def test_native_wire_requires_explicit_provenance():
    bundle = snapshots()
    for entry in bundle[2]["model_list_snapshot"]["model_list"]:
        entry["litellm_params"].pop("api_base")
    for backend in bundle[2]["alias_backends"]["alias//exact"]:
        backend.pop("api_base")
    with pytest.raises(EvidenceError, match="native gateway provenance"):
        normalize(bundle)


def test_native_identity_cannot_override_caller_host_binding():
    models, _, gateway = snapshots()
    entry = gateway["model_list_snapshot"]["model_list"][0]
    backend = gateway["alias_backends"]["alias//exact"][0]
    entry["litellm_params"].pop("api_base")
    backend.pop("api_base")
    gateway["model_list_snapshot"]["model_list"] = [entry]
    gateway["alias_backends"]["alias//exact"] = [backend]
    gateway["native_provenance"] = {
        "alias//exact": {"model": "cohere/command-r", "upstream_host": "cohere"}
    }
    with pytest.raises(EvidenceError, match="native binding"):
        normalize_free_preset_evidence(
            models_dev=models,
            direct_routes=None,
            gateway=gateway,
            trusted_endpoints={},
            trusted_native={("alias//exact", "cohere/command-r"): "opencode"},
            now=NOW,
        )
