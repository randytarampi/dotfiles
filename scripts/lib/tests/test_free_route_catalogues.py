from datetime import datetime, timedelta, timezone

import pytest

from free_preset_evidence import normalize_free_preset_evidence
from free_route_catalogues import (
    CatalogueCollectionError,
    collect_free_route_catalogues,
)

NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
TRUSTED = {
    "https://opencode.ai/zen/v1": "opencode",
    "https://openrouter.ai/api/v1": "openrouter",
}


def test_collects_exact_ids_and_resolves_through_evidence_adapter():
    calls = []

    def fetch(url, key, *, strict):
        calls.append((url, key, strict))
        if "opencode.ai" in url:
            return {"data": [{"id": "big-pickle"}, {"id": "vendor/a:b"}]}
        return {"data": [{"id": "vendor/model:free"}]}

    result = collect_free_route_catalogues(
        [
            {
                "provider_id": "opencode",
                "upstream_host": "opencode",
                "api_base": "https://opencode.ai/zen/v1",
                "credential_key": "oc",
            },
            {
                "provider_id": "openrouter",
                "upstream_host": "openrouter",
                "api_base": "https://openrouter.ai/api/v1",
                "credential_key": "or",
            },
        ],
        trusted_endpoints=TRUSTED,
        credentials={"oc": "secret-one", "or": "secret-two"},
        fetch=fetch,
        clock=lambda: NOW,
    )
    snap = result["direct_routes"]
    assert calls == [
        ("https://opencode.ai/zen/v1/models", "secret-one", True),
        ("https://openrouter.ai/api/v1/models", "secret-two", True),
    ]
    rows = {row["serving_ref"]: row for row in snap["routes"]}
    assert "opencode/big-pickle" in rows
    assert rows["opencode/big-pickle"]["metadata_model_id"] == "big-pickle"
    assert rows["opencode/vendor/a:b"]["model_id"] == "vendor/a:b"
    assert "openrouter/vendor/model:free" in rows
    models_dev = {
        "observed_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        "source": "fixture",
        "models": {
            "opencode": {"models": {"big-pickle": {}}},
            "openrouter": {"models": {"vendor/model:free": {}}},
        },
    }
    candidates = normalize_free_preset_evidence(
        models_dev=models_dev,
        direct_routes=snap,
        gateway=None,
        trusted_endpoints=TRUSTED,
        now=NOW,
    )
    assert {candidate.serving_ref for candidate in candidates} == set(rows)
    assert (
        next(
            c for c in candidates if c.serving_ref == "opencode/big-pickle"
        ).upstream_host
        == "opencode"
    )


def test_empty_inventory_is_success_but_missing_credential_is_unknown():
    endpoint = {
        "provider_id": "opencode",
        "upstream_host": "opencode",
        "api_base": "https://opencode.ai/zen/v1",
        "credential_key": "key",
    }
    result = collect_free_route_catalogues(
        [endpoint],
        trusted_endpoints=TRUSTED,
        credentials={"key": "x"},
        fetch=lambda *a, **k: {"data": []},
        clock=lambda: NOW,
    )
    assert result["direct_routes"]["routes"] == []
    with pytest.raises(CatalogueCollectionError, match="credential missing"):
        collect_free_route_catalogues(
            [endpoint],
            trusted_endpoints=TRUSTED,
            credentials={},
            fetch=lambda *a, **k: {"data": []},
            clock=lambda: NOW,
        )


def test_failure_is_atomic_and_credentials_are_not_in_errors():
    endpoints = [
        {
            "provider_id": "opencode",
            "upstream_host": "opencode",
            "api_base": "https://opencode.ai/zen/v1",
            "credential_key": "one",
        },
        {
            "provider_id": "openrouter",
            "upstream_host": "openrouter",
            "api_base": "https://openrouter.ai/api/v1",
            "credential_key": "two",
        },
    ]
    calls = []

    def fetch(url, key, **kwargs):
        calls.append(url)
        if len(calls) == 2:
            raise RuntimeError("secret-two leaked")
        return {"data": [{"id": "x"}]}

    with pytest.raises(CatalogueCollectionError) as error:
        collect_free_route_catalogues(
            endpoints,
            trusted_endpoints=TRUSTED,
            credentials={"one": "secret-one", "two": "secret-two"},
            fetch=fetch,
            clock=lambda: NOW,
        )
    assert "secret" not in str(error.value)
    assert len(calls) == 2


def test_rejects_untrusted_origin_duplicate_ids_and_unsupported_cohere():
    endpoint = {
        "provider_id": "opencode",
        "upstream_host": "opencode",
        "api_base": "https://evil.example/v1",
        "credential_key": "key",
    }
    with pytest.raises(CatalogueCollectionError, match="not caller-trusted"):
        collect_free_route_catalogues(
            [endpoint],
            trusted_endpoints=TRUSTED,
            credentials={"key": "x"},
            fetch=lambda *a, **k: {},
            clock=lambda: NOW,
        )
    endpoint["api_base"] = "https://opencode.ai/zen/v1"
    with pytest.raises(CatalogueCollectionError, match="duplicate"):
        collect_free_route_catalogues(
            [endpoint],
            trusted_endpoints=TRUSTED,
            credentials={"key": "x"},
            fetch=lambda *a, **k: {"data": [{"id": "x"}, {"id": "x"}]},
            clock=lambda: NOW,
        )
    cohere = {
        "provider_id": "cohere",
        "upstream_host": "cohere",
        "api_base": "https://api.cohere.ai",
        "credential_key": "key",
    }
    with pytest.raises(CatalogueCollectionError, match="unsupported"):
        collect_free_route_catalogues(
            [cohere],
            trusted_endpoints={"https://api.cohere.ai": "cohere"},
            credentials={"key": "x"},
            fetch=lambda *a, **k: {},
            clock=lambda: NOW,
        )


def test_records_completion_time_and_does_not_write_cache(tmp_path):
    times = iter([NOW + timedelta(seconds=3)])
    before = set(tmp_path.iterdir())
    result = collect_free_route_catalogues(
        [
            {
                "provider_id": "opencode",
                "upstream_host": "opencode",
                "api_base": "https://opencode.ai/zen/v1",
                "credential_key": "key",
            }
        ],
        trusted_endpoints=TRUSTED,
        credentials={"key": "x"},
        fetch=lambda *a, **k: {"data": []},
        clock=lambda: next(times),
    )
    assert (
        result["direct_routes"]["observed_at"]
        == (NOW + timedelta(seconds=3)).isoformat()
    )
    assert set(tmp_path.iterdir()) == before


def test_delayed_second_provider_cannot_refresh_first_provider_window():
    times = iter([NOW, NOW + timedelta(minutes=16)])
    endpoints = [
        {
            "provider_id": "opencode",
            "upstream_host": "opencode",
            "api_base": "https://opencode.ai/zen/v1",
            "credential_key": "key",
        },
        {
            "provider_id": "openrouter",
            "upstream_host": "openrouter",
            "api_base": "https://openrouter.ai/api/v1",
            "credential_key": "key",
        },
    ]
    with pytest.raises(CatalogueCollectionError, match="windows do not overlap"):
        collect_free_route_catalogues(
            endpoints,
            trusted_endpoints=TRUSTED,
            credentials={"key": "x"},
            fetch=lambda *a, **k: {"data": []},
            clock=lambda: next(times),
            ttl=timedelta(minutes=15),
        )


@pytest.mark.parametrize("credential", ["", "   "])
def test_whitespace_or_empty_required_credential_is_unknown(credential):
    endpoint = {
        "provider_id": "opencode",
        "upstream_host": "opencode",
        "api_base": "https://opencode.ai/zen/v1",
        "credential_key": "key",
    }
    with pytest.raises(CatalogueCollectionError, match="credential missing"):
        collect_free_route_catalogues(
            [endpoint],
            trusted_endpoints=TRUSTED,
            credentials={"key": credential},
            fetch=lambda *a, **k: {"data": []},
            clock=lambda: NOW,
        )


def test_endpoint_url_uses_established_path_normalizer():
    calls = []
    collect_free_route_catalogues(
        [
            {
                "provider_id": "opencode",
                "upstream_host": "opencode",
                "api_base": "https://opencode.ai/zen/v1/chat/completions",
                "credential_key": "key",
            }
        ],
        trusted_endpoints={"https://opencode.ai/zen/v1/chat/completions": "opencode"},
        credentials={"key": "x"},
        fetch=lambda url, *_args, **_kwargs: (calls.append(url) or {"data": []}),
        clock=lambda: NOW,
    )
    assert calls == ["https://opencode.ai/zen/v1/models"]
