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
    snapshots = fetch_snapshots("placeholder", opener=upstream)
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
    snapshots = fetch_snapshots("placeholder", opener=StubOpener())
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
    snapshots = fetch_snapshots("placeholder", opener=StubOpener())
    store_snapshots(snapshots, tmp_path)
    original = {
        name.name: name.read_bytes() for name in tmp_path.glob("benchlm-*.json")
    }
    with pytest.raises(BenchlmError):
        fetch_snapshots("placeholder", opener=StubOpener(invalid="agentic"))
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
    snapshots = fetch_snapshots("placeholder", opener=StubOpener())
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
    assert result["benchlm_maturity"] == "Supported"


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

    snapshots = fetch_snapshots("placeholder", opener=StubOpener())
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

    store_snapshots(fetch_snapshots("placeholder", opener=StubOpener()), tmp_path)
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
        monkeypatch.setenv("DOTFILES_USE_MODEL_EVIDENCE_RANKING", "1")
        preferred = tier_resolve.resolve_roles_from_list(fleet)
        (tmp_path / "benchlm-reasoning.json").unlink()
        recovered = tier_resolve.resolve_roles_from_list(fleet)
    assert baseline["code-gen"] == "ollama/coder-beta:8b"
    assert preferred["code-gen"] == "ollama/coder-alpha:7b"
    assert recovered["code-gen"] == baseline["code-gen"]


def test_no_network_when_cache_read(monkeypatch, tmp_path):
    store_snapshots(fetch_snapshots("placeholder", opener=StubOpener()), tmp_path)
    monkeypatch.setattr(
        "urllib.request.build_opener",
        lambda *a: (_ for _ in ()).throw(AssertionError("network prohibited")),
    )
    assert len(offline_sources(tmp_path)) == 4
