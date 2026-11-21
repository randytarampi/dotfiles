"""Synthetic adversarial tests: BenchLM Free private ranking evidence."""

import json
from datetime import datetime, timezone
from io import BytesIO
from urllib.error import HTTPError

import pytest

from benchlm_evidence import (
    BenchlmError,
    _parse_page,
    _parse_row,
    fetch_snapshots,
    probe_surface,
    offline_sources,
    store_snapshots,
)
from model_evidence import rank_qualified

DATE = datetime.now(timezone.utc).isoformat()


class Response:
    status = 200

    def __init__(self, data):
        self.data = BytesIO(json.dumps(data).encode())

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, count):
        return self.data.read(count)


class StubOpener:
    def __init__(self, *, invalid=None):
        self.calls = []
        self.invalid = invalid

    def open(self, request, timeout):
        self.calls.append(request)
        surface = request.full_url.split("surface=", 1)[1].split("&", 1)[0]
        if surface == self.invalid:
            raise HTTPError(request.full_url, 429, "rate limit", {}, None)
        return Response(
            {
                "surface": surface,
                "dataAsOf": DATE,
                "methodologyVersion": "6.0",
                "items": [
                    {
                        "modelKey": "provider:low",
                        "creator": "provider",
                        "score": 32.0,
                        "evidenceLabel": "Supported",
                    },
                    {
                        "modelKey": "provider:high",
                        "creator": "provider",
                        "score": 84.0,
                        "evidenceLabel": "Supported",
                    },
                    {
                        "modelKey": "provider:estimate",
                        "creator": "provider",
                        "score": 99.0,
                        "evidenceLabel": "Estimated",
                    },
                ],
                "pagination": {
                    "offset": 0,
                    "limit": 100,
                    "total": 3,
                    "nextOffset": None,
                },
            }
        )


def test_supported_current_surfaces_are_private_and_scale_is_per_surface(tmp_path):
    upstream = StubOpener()
    snapshots = fetch_snapshots("placeholder", opener=upstream, request_interval=0)
    assert len(upstream.calls) == 4
    assert len(snapshots) == 4
    assert {s["metric"] for s in snapshots} == {
        "overall",
        "reasoning",
        "coding",
        "agentic",
    }
    assert all(len(s["models"]) == 2 for s in snapshots)
    assert all("estimate" not in str(s["models"]) for s in snapshots)
    store_snapshots(snapshots, tmp_path)
    assert len(offline_sources(tmp_path)) == 4
    assert all(
        (tmp_path / (s["source"] + ".json")).stat().st_mode & 0o077 == 0
        for s in snapshots
    )
    assert all(
        req.get_header("Authorization") == "Bearer placeholder"
        for req in upstream.calls
    )


def test_coding_evidence_can_reorder_exact_local_candidate_without_cross_scale(
    tmp_path,
):
    snapshots = fetch_snapshots("placeholder", opener=StubOpener(), request_interval=0)
    store_snapshots(snapshots, tmp_path)
    loaded = offline_sources(tmp_path)
    coding = next(source for source in loaded if source["metric"] == "coding")
    identities = {
        "omlx/low": {
            "benchlm-coding": {
                "model_id": "provider:low",
                "creator_id": "provider",
                "scale": coding["scale"],
            }
        },
        "omlx/high": {
            "benchlm-coding": {
                "model_id": "provider:high",
                "creator_id": "provider",
                "scale": coding["scale"],
            }
        },
    }
    entries = [
        {"name": name, "provider": "omlx", "capabilities": ["tools"]}
        for name in ("low", "high")
    ]
    assert (
        rank_qualified({"code-gen": entries}, catalogues=loaded, mappings=identities)
        == 1
    )
    assert [x["name"] for x in entries] == ["high", "low"]


def test_rate_limit_and_wrong_key_preserve_last_good(tmp_path):
    snapshots = fetch_snapshots("placeholder", opener=StubOpener(), request_interval=0)
    store_snapshots(snapshots, tmp_path)
    original = {
        name.name: name.read_bytes() for name in tmp_path.glob("benchlm-*.json")
    }
    with pytest.raises(BenchlmError):
        fetch_snapshots(
            "placeholder", opener=StubOpener(invalid="agentic"), request_interval=0
        )
    assert original == {
        name.name: name.read_bytes() for name in tmp_path.glob("benchlm-*.json")
    }
    with pytest.raises(BenchlmError, match="missing"):
        fetch_snapshots("", opener=StubOpener())


@pytest.mark.parametrize(
    "failure", ["wrong_surface", "no_version", "bad_cursor", "bad_date"]
)
def test_invalid_envelope_and_pagination_fail_closed(failure):
    page = {
        "surface": "coding",
        "dataAsOf": DATE,
        "methodologyVersion": "6.0",
        "items": [{"modelKey": "x"}],
        "pagination": {"offset": 0, "total": 1, "nextOffset": None},
    }
    if failure == "wrong_surface":
        page["surface"] = "agentic"
    if failure == "no_version":
        del page["methodologyVersion"]
    if failure == "bad_cursor":
        page["pagination"]["nextOffset"] = 0
    if failure == "bad_date":
        page["dataAsOf"] = "bogus"
    with pytest.raises(BenchlmError):
        _parse_page(page, surface="coding", offset=0)


def test_estimated_and_missing_creator_never_promote():
    seen = set()
    assert (
        _parse_row(
            {
                "modelKey": "one",
                "creator": "maker",
                "score": 90,
                "evidenceLabel": "Estimated",
            },
            seen,
        )
        is None
    )
    with pytest.raises(BenchlmError, match="creator"):
        _parse_row({"modelKey": "two", "score": 80, "evidenceLabel": "Supported"}, seen)


def test_rejects_duplicate_identity_and_wrong_source_set(tmp_path):
    snapshots = fetch_snapshots("placeholder", opener=StubOpener(), request_interval=0)
    with pytest.raises(BenchlmError, match="partial"):
        store_snapshots(snapshots[:2], tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_supported_does_not_claim_every_underlying_result_was_independent():
    seen = set()
    result = _parse_row(
        {
            "modelKey": "x",
            "creator": "maker",
            "score": 58,
            "evidenceLabel": "Supported",
        },
        seen,
    )
    assert result["evidence"] == "mixed"
    assert result["evidence_status"] == "Supported"
    assert result["benchlm_maturity"] == "unknown"


def test_version_and_total_must_agree_across_paged_response():
    first = {
        "surface": "coding",
        "dataAsOf": DATE,
        "methodologyVersion": "6.0",
        "items": [{"modelKey": "first"}],
        "pagination": {"offset": 0, "total": 2, "nextOffset": 1},
    }
    _, next_offset, revision = _parse_page(first, surface="coding", offset=0)
    second = {
        **first,
        "items": [{"modelKey": "second"}],
        "pagination": {"offset": next_offset, "total": 3, "nextOffset": 2},
    }
    with pytest.raises(BenchlmError, match="mixed scoring revisions"):
        _parse_page(second, surface="coding", offset=1, reference=revision)


def test_duplicate_identity_rejected_even_if_unscored():
    seen = set()
    item = {
        "modelKey": "same",
        "creator": "maker",
        "evidenceLabel": "Estimated",
        "score": 15,
    }
    assert _parse_row(item, seen) is None
    with pytest.raises(BenchlmError, match="duplicate"):
        _parse_row(item, seen)


def test_partial_or_mixed_batch_is_ineligible_for_resolution(tmp_path):
    from model_evidence import load_catalogues

    snapshots = fetch_snapshots("placeholder", opener=StubOpener(), request_interval=0)
    store_snapshots(snapshots, tmp_path)
    assert len(load_catalogues(tmp_path, aa_evidence={})) == 4
    missing = tmp_path / "benchlm-agentic.json"
    missing.unlink()
    assert load_catalogues(tmp_path, aa_evidence={}) == []
    store_snapshots(snapshots, tmp_path)
    stale = json.loads(missing.read_text(encoding="utf-8"))
    stale["bundle"]["id"] = "older-generation"
    missing.write_text(json.dumps(stale), encoding="utf-8")
    assert load_catalogues(tmp_path, aa_evidence={}) == []


def test_invalid_pagination_type_is_bounded_error():
    data = {
        "surface": "coding",
        "dataAsOf": DATE,
        "methodologyVersion": "6.0",
        "items": [{"modelKey": "first"}],
        "pagination": "not-an-object",
    }
    with pytest.raises(BenchlmError, match="pagination"):
        _parse_page(data, surface="coding", offset=0)


def test_real_tier_consumer_uses_benchlm_only_with_opt_in_and_complete_cache(
    monkeypatch, tmp_path
):
    from unittest.mock import patch

    import model_evidence
    import tier_resolve

    fleet = [
        {"name": "coder-alpha:7b", "provider": "ollama", "param_count": 7},
        {"name": "coder-beta:8b", "provider": "ollama", "param_count": 8},
    ]

    def details(name, provider="ollama"):
        return {
            "param_count": 7 if "alpha" in name else 8,
            "capabilities": ["completion", "thinking", "tools"],
            "is_moe": False,
        }

    store_snapshots(
        fetch_snapshots("placeholder", opener=StubOpener(), request_interval=0),
        tmp_path,
    )
    mapping = {
        "schema_version": 1,
        "mappings": {
            "ollama/coder-alpha:7b": {
                "benchlm-coding": {
                    "model_id": "provider:high",
                    "creator_id": "provider",
                    "scale": "benchalign-6.0-coding",
                }
            },
            "ollama/coder-beta:8b": {
                "benchlm-coding": {
                    "model_id": "provider:low",
                    "creator_id": "provider",
                    "scale": "benchalign-6.0-coding",
                }
            },
        },
    }
    mapping_file = tmp_path / "private-map.json"
    mapping_file.write_text(json.dumps(mapping), encoding="utf-8")
    monkeypatch.setattr(model_evidence, "sources_dir", lambda: tmp_path)
    monkeypatch.setattr(model_evidence, "load_snapshot", lambda **kw: None)
    monkeypatch.setenv("DOTFILES_MODEL_EVIDENCE_MAP", str(mapping_file))
    monkeypatch.delenv("DOTFILES_USE_MODEL_EVIDENCE_RANKING", raising=False)
    with patch.object(tier_resolve, "get_model_details", side_effect=details):
        baseline = tier_resolve.resolve_roles_from_list(fleet)
        proposed = tier_resolve.resolve_roles_from_list(fleet, evidence_mode="shadow")
        preferred = tier_resolve.resolve_roles_from_list(fleet, evidence_mode="apply")
        (tmp_path / "benchlm-reasoning.json").unlink()
        recovered = tier_resolve.resolve_roles_from_list(fleet, evidence_mode="apply")
    assert baseline["code-gen"] == "ollama/coder-beta:8b"
    assert proposed == baseline
    assert preferred["code-gen"] == "ollama/coder-alpha:7b"
    assert recovered["code-gen"] == baseline["code-gen"]


def test_no_network_when_cache_read(monkeypatch, tmp_path):
    store_snapshots(
        fetch_snapshots("placeholder", opener=StubOpener(), request_interval=0),
        tmp_path,
    )
    monkeypatch.setattr(
        "urllib.request.build_opener",
        lambda *a: (_ for _ in ()).throw(AssertionError("network prohibited")),
    )
    assert len(offline_sources(tmp_path)) == 4


def test_one_request_probe_never_writes_scores_or_follows_pagination(tmp_path):
    upstream = StubOpener()
    result = probe_surface("placeholder", surface="coding", opener=upstream)
    assert result["supported"] == 2
    assert len(upstream.calls) == 1
    assert list(tmp_path.iterdir()) == []


def test_rate_limit_is_visible_without_response_or_credentials():
    class LimitedOpener:
        def open(self, request, timeout):
            raise HTTPError(
                request.full_url,
                429,
                "rate limited",
                {"Retry-After": "45"},
                None,
            )

    with pytest.raises(BenchlmError, match="HTTP 429.*45 seconds") as error:
        probe_surface("private-value", opener=LimitedOpener())
    assert "private-value" not in str(error.value)


def test_refresh_request_budget_stops_before_extra_pages():
    from urllib.parse import parse_qs, urlsplit

    class PagedOpener(StubOpener):
        def open(self, request, timeout):
            response = super().open(request, timeout)
            result = json.loads(response.data.getvalue())
            offset = int(parse_qs(urlsplit(request.full_url).query)["offset"][0])
            # Distinct page identities ensure this test reaches the budget
            # boundary, rather than failing earlier at duplicate validation.
            result["items"] = [
                {**item, "modelKey": item["modelKey"] + ":" + str(offset)}
                for item in result["items"]
            ]
            result["pagination"] = {
                "offset": offset,
                "limit": 100,
                "total": 30,
                "nextOffset": offset + len(result["items"]),
            }
            return Response(result)

    upstream = PagedOpener()
    with pytest.raises(BenchlmError, match="budget exceeded"):
        fetch_snapshots(
            "placeholder",
            opener=upstream,
            max_requests=4,
            request_interval=0,
        )
    assert len(upstream.calls) == 4


def test_pacing_is_enforced_between_requests_without_real_sleep():
    upstream = StubOpener()
    now = [0.0]
    delays = []

    def sleep(seconds):
        delays.append(seconds)
        now[0] += seconds

    fetch_snapshots(
        "placeholder",
        opener=upstream,
        request_interval=2.1,
        clock=lambda: now[0],
        sleeper=sleep,
    )
    assert len(upstream.calls) == 4
    assert len(delays) == 3
    assert delays == [2.1] * 3


def test_request_budget_prevents_unbounded_pagination():
    upstream = StubOpener()
    fetch_snapshots(
        "placeholder",
        opener=upstream,
        max_requests=4,
        request_interval=0,
    )
    assert len(upstream.calls) == 4


def test_live_evidence_status_is_not_an_alias_for_maturity():
    seen = set()
    supported = _parse_row(
        {
            "modelKey": "maker:released",
            "creator": "maker",
            "score": 72.0,
            "evidenceStatus": "supported",
            "maturity": "developing",
        },
        seen,
    )
    assert supported["evidence_status"] == "Supported"
    assert supported["benchlm_maturity"] == "developing"
    assert (
        _parse_row(
            {
                "modelKey": "maker:preliminary",
                "creator": "maker",
                "score": 81.0,
                "evidenceStatus": "estimated",
                "maturity": "preliminary",
            },
            seen,
        )
        is None
    )


def test_live_ranking_mode_and_available_surfaces_do_not_conflate_methods():
    payload = {
        "surface": "coding",
        "dataAsOf": DATE,
        "methodologyVersion": "bench-align-v6.0-2026-11-19",
        "rankingMode": "bench-align-v5",
        "availableSurfaces": ["coding", "overall", "knowledge"],
        "planNotice": "Free limited to the first 100 rows after 2026-12-05",
        "items": [{"modelKey": "m", "creator": "vendor"}],
        "pagination": {"offset": 0, "total": 1, "nextOffset": None},
    }
    _, _, revision = _parse_page(payload, surface="coding", offset=0)
    assert revision[1] == "bench-align-v6.0-2026-11-19"
    assert revision[3] == "bench-align-v5"
    payload["availableSurfaces"] = ["reasoning"]
    with pytest.raises(BenchlmError, match="available surfaces"):
        _parse_page(payload, surface="coding", offset=0)


def test_free_first_100_requires_explicit_coverage_and_unique_scale(tmp_path):
    from urllib.parse import parse_qs, urlsplit

    from benchlm_evidence import SURFACES

    class FreePlanOpener:
        def __init__(self):
            self.calls = []

        def open(self, request, timeout):
            params = parse_qs(urlsplit(request.full_url).query)
            offset = int(params["offset"][0])
            surface = params["surface"][0]
            self.calls.append((surface, offset))
            return Response(
                {
                    "surface": surface,
                    "dataAsOf": DATE,
                    "methodologyVersion": "v6.0",
                    "rankingMode": "bench-align-v5",
                    "availableSurfaces": list(SURFACES),
                    "planNotice": "Free capped to the first 100 rows",
                    "items": [
                        {
                            "modelKey": f"vendor:{surface}:{i}",
                            "creator": "vendor",
                            "evidenceStatus": "supported",
                            "maturity": "developing",
                            "score": 65.0,
                        }
                        for i in range(offset, offset + 25)
                    ],
                    "pagination": {
                        "offset": offset,
                        "total": 214,
                        "nextOffset": offset + 25 if offset + 25 < 100 else None,
                    },
                }
            )

    upstream = FreePlanOpener()
    with pytest.raises(BenchlmError, match="truncated"):
        fetch_snapshots(
            "placeholder",
            opener=upstream,
            max_requests=20,
            coverage_mode="complete",
            request_interval=0,
        )
    upstream = FreePlanOpener()
    snapshots = fetch_snapshots(
        "placeholder",
        opener=upstream,
        max_requests=20,
        coverage_mode="first-100",
        request_interval=0,
    )
    assert len(upstream.calls) == 16
    assert all(
        source["coverage"]
        == {
            "mode": "first-100",
            "retrieved": 100,
            "total": 214,
        }
        for source in snapshots
    )
    assert all(source["scale"].endswith("-top100") for source in snapshots)
    store_snapshots(snapshots, tmp_path)
    assert len(offline_sources(tmp_path)) == 4


def test_first_100_budget_fails_before_network():
    upstream = StubOpener()
    with pytest.raises(BenchlmError, match="at least 16 reads"):
        fetch_snapshots(
            "placeholder",
            opener=upstream,
            coverage_mode="first-100",
            request_interval=0,
        )
    assert upstream.calls == []
