"""Synthetic adversarial tests: BenchLM Free private ranking evidence."""

import json
import stat
from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path
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
                "rankingMode": "bench-align-v5",
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


def generation_dir(root):
    manifest = json.loads((Path(root) / "manifest.json").read_text(encoding="utf-8"))
    return Path(root) / "generations" / manifest["generation"]


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
    directory = generation_dir(tmp_path)
    assert all(
        (directory / (s["source"] + ".json")).stat().st_mode & 0o077 == 0
        for s in snapshots
    )
    assert all(
        req.get_header("Authorization") == "Bearer placeholder"
        for req in upstream.calls
    )


def test_rate_limit_and_wrong_key_preserve_last_good(tmp_path):
    snapshots = fetch_snapshots("placeholder", opener=StubOpener(), request_interval=0)
    store_snapshots(snapshots, tmp_path)
    original = {
        str(name.relative_to(tmp_path)): name.read_bytes()
        for name in sorted(tmp_path.rglob("*.json"))
    }
    with pytest.raises(BenchlmError):
        fetch_snapshots(
            "placeholder", opener=StubOpener(invalid="agentic"), request_interval=0
        )
    assert original == {
        str(name.relative_to(tmp_path)): name.read_bytes()
        for name in sorted(tmp_path.rglob("*.json"))
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
        "rankingMode": "bench-align-v5",
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
    assert result["benchlm_evidence_status"] == "supported"


def test_version_and_total_must_agree_across_paged_response():
    first = {
        "surface": "coding",
        "dataAsOf": DATE,
        "methodologyVersion": "6.0",
        "rankingMode": "bench-align-v5",
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
    missing = generation_dir(tmp_path) / "benchlm-agentic.json"
    missing.unlink()
    assert load_catalogues(tmp_path, aa_evidence={}) == []
    fresh = fetch_snapshots("placeholder", opener=StubOpener(), request_interval=0)
    store_snapshots(fresh, tmp_path)
    directory = generation_dir(tmp_path)
    stale = json.loads((directory / "benchlm-agentic.json").read_text(encoding="utf-8"))
    stale["bundle"]["id"] = "older-generation"
    (directory / "benchlm-agentic.json").write_text(json.dumps(stale), encoding="utf-8")
    assert load_catalogues(tmp_path, aa_evidence={}) == []


def test_invalid_pagination_type_is_bounded_error():
    data = {
        "surface": "coding",
        "dataAsOf": DATE,
        "methodologyVersion": "6.0",
        "rankingMode": "bench-align-v5",
        "items": [{"modelKey": "first"}],
        "pagination": "not-an-object",
    }
    with pytest.raises(BenchlmError, match="pagination"):
        _parse_page(data, surface="coding", offset=0)


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
    assert "limit=25" in upstream.calls[0].full_url
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


def test_live_status_is_not_model_maturity():
    row = _parse_row(
        {
            "modelKey": "provider:qwen",
            "creator": "provider",
            "score": 76.2,
            "evidenceStatus": "supported",
            "maturity": "developing",
        },
        set(),
    )
    assert row["benchlm_evidence_status"] == "supported"
    assert row["benchlm_maturity"] == "developing"


def test_live_unsupported_status_is_excluded_even_if_maturity_supported():
    assert (
        _parse_row(
            {
                "modelKey": "provider:estimate",
                "creator": "provider",
                "score": 80.0,
                "evidenceStatus": "estimated",
                "maturity": "supported",
            },
            set(),
        )
        is None
    )


def test_explicit_partial_window_preserves_true_population_and_scale():
    class FreeOpener(StubOpener):
        def open(self, request, timeout):
            response = super().open(request, timeout)
            envelope = json.loads(response.data.getvalue())
            envelope["availableSurfaces"] = list(
                ("coding", "reasoning", "overall", "agentic")
            )
            envelope["planNotice"] = (
                "From December 5, 2026, Free returns 25 rows a read and "
                "the first 100 rows of a list."
            )
            envelope["rankingMode"] = "bench-align-v5"
            envelope["pagination"]["total"] = 214
            # Simulate four successive 25-row pages and 100-row Free ceiling.
            from urllib.parse import parse_qs, urlsplit

            offset = int(parse_qs(urlsplit(request.full_url).query)["offset"][0])
            envelope["items"] = [
                {
                    "modelKey": "provider:" + str(i),
                    "creator": "provider",
                    "score": float(i % 100),
                    "evidenceStatus": "supported",
                    "maturity": "developing",
                }
                for i in range(offset, offset + 25)
            ]
            envelope["pagination"]["offset"] = offset
            envelope["pagination"]["nextOffset"] = offset + 25 if offset < 75 else None
            return Response(envelope)

    upstream = FreeOpener()
    snapshots = fetch_snapshots(
        "placeholder",
        opener=upstream,
        max_requests=16,
        request_interval=0,
        coverage_mode="first-100",
    )
    assert len(upstream.calls) == 16
    assert all("limit=25" in call.full_url for call in upstream.calls)
    assert all(row["retrieved_rows"] == 100 for row in snapshots)
    assert all(row["coverage"] == "first-100-subset" for row in snapshots)
    assert all(row["population_total"] == 214 for row in snapshots)
    assert all("bench-align-v5" in row["scale"] for row in snapshots)
    # A bounded prefix must not share a scale with a complete dataset.
    assert all(row["scale"].endswith("-top100") for row in snapshots)


def test_incomplete_first_100_window_is_rejected():
    example = {
        "surface": "coding",
        "dataAsOf": DATE,
        "methodologyVersion": "6.0",
        "rankingMode": "bench-align-v5",
        "items": [{"modelKey": "x", "creator": "provider", "score": 5}],
        "pagination": {"offset": 0, "total": 214, "nextOffset": None},
    }
    with pytest.raises(BenchlmError, match="truncated"):
        _parse_page(example, surface="coding", offset=0, coverage_mode="first-100")


def test_live_availability_objects_are_accepted():
    """Free responses advertise surfaces as objects with a `surface` key."""
    payload = {
        "surface": "coding",
        "dataAsOf": DATE,
        "methodologyVersion": "6.0",
        "rankingMode": "bench-align-v5",
        "availableSurfaces": [
            {"surface": "overall", "count": 214},
            {"surface": "coding", "count": 179},
        ],
        "items": [
            {"modelKey": "a", "creator": "maker", "score": 10},
            {"modelKey": "b", "creator": "maker", "score": 20},
        ],
        "pagination": {"offset": 0, "total": 2, "nextOffset": None},
    }
    rows, cursor, _ = _parse_page(payload, surface="coding", offset=0)
    assert len(rows) == 2 and cursor is None

    payload["availableSurfaces"] = ["overall", "coding"]
    rows, _, _ = _parse_page(payload, surface="coding", offset=0)
    assert len(rows) == 2

    payload["availableSurfaces"] = [{"surface": "overall"}]
    with pytest.raises(BenchlmError, match="absent from advertised availability"):
        _parse_page(payload, surface="coding", offset=0)


def test_complete_refresh_is_refused_from_free_cap_date():
    upstream = StubOpener()
    with pytest.raises(BenchlmError, match="first-100"):
        fetch_snapshots(
            "placeholder",
            opener=upstream,
            request_interval=0,
            today=date(2026, 12, 5),
        )
    assert upstream.calls == []


def test_first_100_budget_is_validated_before_transport():
    upstream = StubOpener()
    with pytest.raises(BenchlmError, match="budget"):
        fetch_snapshots(
            "placeholder",
            opener=upstream,
            request_interval=0,
            coverage_mode="first-100",
            max_requests=4,
        )
    assert upstream.calls == []


def test_first_100_default_budget_completes_and_labels_coverage():
    upstream = StubOpener()
    snapshots = fetch_snapshots(
        "placeholder",
        opener=upstream,
        request_interval=0,
        coverage_mode="first-100",
        today=date(2026, 12, 5),
    )
    assert len(snapshots) == 4
    assert all(row["coverage"] == "first-100-subset" for row in snapshots)
    assert all(row["scale"].endswith("-top100") for row in snapshots)


def test_interrupted_generation_keeps_previous_manifest(tmp_path, monkeypatch):
    snapshots = fetch_snapshots("placeholder", opener=StubOpener(), request_interval=0)
    store_snapshots(snapshots, tmp_path)
    before = (tmp_path / "manifest.json").read_bytes()
    import benchlm_evidence

    original = benchlm_evidence._atomic_private
    count = {"n": 0}

    def failing(path, payload):
        count["n"] += 1
        if count["n"] == 3:
            raise OSError("disk full")
        return original(path, payload)

    monkeypatch.setattr(benchlm_evidence, "_atomic_private", failing)
    fresh = fetch_snapshots("placeholder", opener=StubOpener(), request_interval=0)
    with pytest.raises(OSError):
        store_snapshots(fresh, tmp_path)
    assert (tmp_path / "manifest.json").read_bytes() == before
    assert len(offline_sources(tmp_path)) == 4


def test_offline_rejects_mixed_coverage(tmp_path):
    snapshots = fetch_snapshots("placeholder", opener=StubOpener(), request_interval=0)
    store_snapshots(snapshots, tmp_path)
    path = generation_dir(tmp_path) / "benchlm-coding.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["coverage"] = "first-100-subset"
    payload["retrieved_rows"] = len(payload["models"])
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BenchlmError, match="incomplete or stale|coverage"):
        offline_sources(tmp_path)


def test_store_requires_a_private_preexisting_directory(tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    private.chmod(0o700)
    snapshots = fetch_snapshots("placeholder", opener=StubOpener(), request_interval=0)
    store_snapshots(snapshots, private)
    assert stat.S_IMODE(private.stat().st_mode) == 0o700
    assert (private / "manifest.json").exists()

    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o755)
    with pytest.raises(BenchlmError, match="private"):
        store_snapshots(
            fetch_snapshots("placeholder", opener=StubOpener(), request_interval=0),
            shared,
        )
    assert stat.S_IMODE(shared.stat().st_mode) == 0o755
