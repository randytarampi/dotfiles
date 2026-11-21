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
        return Response({
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
            "pagination": {"offset": 0, "limit": 100, "total": 3, "nextOffset": None},
        })


def test_supported_current_surfaces_are_private_and_scale_is_per_surface(tmp_path):
    upstream = StubOpener()
    snapshots = fetch_snapshots("placeholder", opener=upstream)
    assert len(upstream.calls) == 4
    assert len(snapshots) == 4
    assert {s["metric"] for s in snapshots} == {
        "overall", "reasoning", "coding", "agentic"
    }
    assert all(len(s["models"]) == 2 for s in snapshots)
    assert all("estimate" not in str(s["models"]) for s in snapshots)
    store_snapshots(snapshots, tmp_path)
    assert len(offline_sources(tmp_path)) == 4
    assert all((tmp_path / (s["source"] + ".json")).stat().st_mode & 0o077 == 0
               for s in snapshots)
    assert all(req.get_header("Authorization") == "Bearer placeholder"
               for req in upstream.calls)


def test_coding_evidence_can_reorder_exact_local_candidate_without_cross_scale(tmp_path):
    snapshots = fetch_snapshots("placeholder", opener=StubOpener())
    store_snapshots(snapshots, tmp_path)
    loaded = offline_sources(tmp_path)
    coding = next(source for source in loaded if source["metric"] == "coding")
    identities = {
        "omlx/low": {"benchlm-coding": {
            "model_id": "provider:low", "creator_id": "provider",
            "scale": coding["scale"]
        }},
        "omlx/high": {"benchlm-coding": {
            "model_id": "provider:high", "creator_id": "provider",
            "scale": coding["scale"]
        }},
    }
    entries = [
        {"name": name, "provider": "omlx", "capabilities": ["tools"]}
        for name in ("low", "high")
    ]
    assert rank_qualified({"code-gen": entries}, catalogues=loaded,
                          mappings=identities) == 1
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


@pytest.mark.parametrize("failure", ["wrong_surface", "no_version", "bad_cursor", "bad_date"])
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
    assert _parse_row({
        "modelKey": "one", "creator": "maker",
        "score": 90, "evidenceLabel": "Estimated"
    }, seen) is None
    with pytest.raises(BenchlmError, match="creator"):
        _parse_row({
            "modelKey": "two", "score": 80, "evidenceLabel": "Supported"
        }, seen)


def test_rejects_duplicate_identity_and_wrong_source_set(tmp_path):
    snapshots = fetch_snapshots("placeholder", opener=StubOpener())
    with pytest.raises(BenchlmError, match="snapshot source set"):
        store_snapshots(snapshots[:2], tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_no_network_when_cache_read(monkeypatch, tmp_path):
    store_snapshots(fetch_snapshots("placeholder", opener=StubOpener()), tmp_path)
    monkeypatch.setattr("urllib.request.build_opener", lambda *a: (_ for _ in ()).throw(
        AssertionError("network prohibited")))
    assert len(offline_sources(tmp_path)) == 4
