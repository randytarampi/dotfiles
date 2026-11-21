"""Producer error and refusal paths: sanitised failures, no partial writes."""

import json
import urllib.error
from datetime import datetime, timezone

import pytest

import artificial_analysis as aa
import benchlm_evidence as bl
from model_evidence import _manifest_sources

NOW = datetime(2026, 11, 21, tzinfo=timezone.utc)

AA_PAGE = {
    "tier": "free",
    "pagination": {"page": 1, "total_pages": 1, "has_more": False},
    "intelligence_index_version": 5,
    "data": [
        {
            "id": "x",
            "slug": "x",
            "name": "X",
            "model_creator": {"id": "c", "name": "C"},
            "evaluations": {"artificial_analysis_intelligence_index": 50},
        }
    ],
}

BL_PAYLOAD = {
    "surface": "coding",
    "dataAsOf": "2026-11-21",
    "methodologyVersion": "6.0",
    "rankingMode": "bench-align-v5",
    "availableSurfaces": [{"surface": "coding"}],
    "items": [
        {
            "modelKey": "a",
            "creator": "m",
            "score": 10,
            "evidenceStatus": "supported",
            "maturity": "developing",
        },
        {
            "modelKey": "b",
            "creator": "m",
            "score": 20,
            "evidenceStatus": "supported",
            "maturity": "developing",
        },
    ],
    "pagination": {"offset": 0, "total": 2, "nextOffset": None},
}


class Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def read(self, limit=None):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class Opener:
    def __init__(self, result):
        self.result = result

    def open(self, request, timeout=None):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def http_error(code, retry=None):
    headers = {"Retry-After": retry} if retry is not None else {}
    return urllib.error.HTTPError("https://example", code, "err", headers, None)


@pytest.mark.parametrize(
    "error,message",
    [
        (http_error(429, "7"), "rate limited"),
        (http_error(401), "request failed"),
        (http_error(500), "request failed"),
        (urllib.error.URLError("dns"), "network or JSON failure"),
        (ValueError("bad json"), "network or JSON failure"),
    ],
)
def test_aa_request_errors_are_sanitised(monkeypatch, error, message):
    monkeypatch.setattr(
        aa.urllib.request, "build_opener", lambda handler=None: Opener(error)
    )
    with pytest.raises(aa.EvidenceError, match=message):
        aa._request_page(1, "secret-value")
    try:
        aa._request_page(1, "secret-value")
    except aa.EvidenceError as exc:
        assert "secret-value" not in str(exc)


def test_aa_oversized_page_is_refused(monkeypatch):
    payload = b"x" * (aa.MAX_RESPONSE_BYTES + 1)
    monkeypatch.setattr(
        aa.urllib.request,
        "build_opener",
        lambda handler=None: Opener(Response(payload)),
    )
    with pytest.raises(aa.EvidenceError, match="too large"):
        aa._request_page(1, "k")


def test_aa_write_refuses_symlink_snapshot(tmp_path):
    payload = b"id,slug,name,creator_id,creator_name,intelligence\n"
    aa.write_snapshot(tmp_path, payload, "5", 0)
    snapshots = tmp_path / "snapshots"
    target = next(snapshots.iterdir())
    target.unlink()
    target.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(aa.EvidenceError, match="symlink snapshot"):
        aa.write_snapshot(tmp_path, payload, "5", 0)


def test_aa_load_rejects_invalid_manifests(tmp_path):
    (tmp_path / "manifest.json").write_text("[]", encoding="utf-8")
    assert aa.load_snapshot(tmp_path) is None
    (tmp_path / "manifest.json").write_text(
        json.dumps({"schema_version": 99, "source": aa.ENDPOINT}), encoding="utf-8"
    )
    assert aa.load_snapshot(tmp_path) is None


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda p: p.update(surface="agentic"), "surface mismatch"),
        (lambda p: p.update(availableSurfaces=[1]), "availability malformed"),
        (lambda p: p.update(availableSurfaces=[{"surface": "agentic"}]), "absent"),
        (lambda p: p.update(planNotice="x" * 3000), "plan notice malformed"),
        (lambda p: p.update(dataAsOf="not-a-date"), "date invalid"),
        (lambda p: p.pop("methodologyVersion"), "methodology version missing"),
        (lambda p: p.pop("rankingMode"), "ranking mode missing"),
        (lambda p: p.update(items=[]), "empty or oversized page"),
        (lambda p: p["pagination"].update(offset=1), "offset mismatch"),
        (lambda p: p["pagination"].update(total=1), "outside supported bounds"),
    ],
)
def test_benchlm_parse_refusals(mutate, match):
    payload = json.loads(json.dumps(BL_PAYLOAD))
    mutate(payload)
    with pytest.raises(bl.BenchlmError, match=match):
        bl._parse_page(payload, surface="coding", offset=0)


def test_benchlm_mixed_revision_reference_is_refused():
    first = bl._parse_page(BL_PAYLOAD, surface="coding", offset=0)
    payload = json.loads(json.dumps(BL_PAYLOAD))
    payload["methodologyVersion"] = "7.0"
    with pytest.raises(bl.BenchlmError, match="mixed scoring revisions"):
        bl._parse_page(payload, surface="coding", offset=0, reference=first[2])


@pytest.mark.parametrize(
    "error,message",
    [
        (http_error(429, "9"), "HTTP 429"),
        (http_error(401), "credential or plan"),
        (http_error(403), "credential or plan"),
        (http_error(500), "HTTP error 500"),
        (urllib.error.URLError("dns"), "request or decoding failed"),
    ],
)
def test_benchlm_request_errors_are_sanitised(error, message):
    with pytest.raises(bl.BenchlmError, match=message):
        bl._read_page("coding", 0, "private-value", opener=Opener(error))


def test_benchlm_probe_refusals(monkeypatch):
    with pytest.raises(bl.BenchlmError, match="invalid BenchLM surface"):
        bl.probe_surface("k", surface="nope")
    payload = json.loads(json.dumps(BL_PAYLOAD))
    payload["items"] = [payload["items"][0]]
    payload["pagination"] = {"offset": 0, "total": 1, "nextOffset": None}
    with pytest.raises(bl.BenchlmError, match="insufficient Supported rows"):
        bl.probe_surface(
            "k", surface="coding", opener=Opener(Response(json.dumps(payload).encode()))
        )


def test_benchlm_store_refuses_invalid_sets(tmp_path):
    snapshots = bl.fetch_snapshots(
        "placeholder", opener=StubOpener(), request_interval=0
    )
    with pytest.raises(bl.BenchlmError, match="partial BenchLM snapshot set"):
        bl.store_snapshots(snapshots[:2], tmp_path)
    mixed = [dict(row) for row in snapshots]
    mixed[1]["bundle"] = {"id": "other", "members": mixed[1]["bundle"]["members"]}
    with pytest.raises(bl.BenchlmError, match="mixed snapshot batches"):
        bl.store_snapshots(mixed, tmp_path)


def test_manifest_sources_refusals(tmp_path):
    assert _manifest_sources(tmp_path) is None
    (tmp_path / "manifest.json").write_text("[]", encoding="utf-8")
    assert _manifest_sources(tmp_path) == ([], None)
    (tmp_path / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "generation": "g", "sources": ["../x.json"]}),
        encoding="utf-8",
    )
    assert _manifest_sources(tmp_path) == ([], None)


class StubOpener:
    """Minimal one-page-per-surface opener used only for set validation."""

    def __init__(self):
        self.calls = []

    def open(self, request, timeout=None):
        self.calls.append(request)
        offset = int(request.full_url.split("offset=")[1].split("&")[0])
        surface = request.full_url.split("surface=")[1].split("&")[0]
        payload = json.loads(json.dumps(BL_PAYLOAD))
        payload["surface"] = surface
        payload["availableSurfaces"] = [{"surface": surface}]
        payload["pagination"] = {"offset": offset, "total": 2, "nextOffset": None}
        return Response(json.dumps(payload).encode())
