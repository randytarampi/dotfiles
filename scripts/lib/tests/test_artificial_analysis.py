"""Synthetic, licence-safe Artificial Analysis Free API regression tests."""

from datetime import datetime, timedelta, timezone

import hashlib
import json

import urllib.error
import urllib.request

import pytest

from artificial_analysis import (
    EvidenceError,
    fetch_free,
    probe_free,
    load_snapshot,
    normalize_pages,
    write_snapshot,
)


def page(
    number=1,
    total=1,
    *,
    model_id="synthetic-a",
    coding=25,
    intelligence=10,
    version=4.1,
):
    return {
        "tier": "free",
        "intelligence_index_version": version,
        "pagination": {
            "page": number,
            "total_pages": total,
            "page_size": 200,
            "has_more": number < total,
        },
        "data": [
            {
                "id": model_id,
                "slug": model_id,
                "name": "Synthetic " + model_id,
                "model_creator": {"id": "synthetic-vendor", "name": "Synthetic"},
                "evaluations": {
                    "artificial_analysis_intelligence_index": intelligence,
                    "artificial_analysis_coding_index": coding,
                    "artificial_analysis_agentic_index": None,
                },
            }
        ],
    }


def test_free_api_two_pages_and_missing_scores():
    calls = []

    def getter(number):
        calls.append(number)
        return page(number, 2, model_id="synthetic-" + str(number))

    payload, version, count = fetch_free("dummy", get_page=getter)
    assert calls == [1, 2]
    assert (version, count) == ("4.1", 2)
    assert b"synthetic-1" in payload
    assert b"synthetic-2" in payload
    assert b"intelligence" in payload
    assert b"agentic" not in payload
    assert b"coding" not in payload
    assert b"slug" in payload
    assert b"name" in payload
    assert b"creator_name" in payload


@pytest.mark.parametrize(
    "pages",
    [
        [page(1, 2)],
        [page(2, 2)],
        [page(1, 2), page(2, 2, model_id="synthetic-a")],
        [page(1, 2), page(2, 2, version=5.0, model_id="synthetic-b")],
        [page(1, 1, intelligence=float("nan"))],
        [page(1, 1, intelligence=True)],
    ],
)
def test_invalid_evidence_is_rejected(pages):
    with pytest.raises(EvidenceError):
        normalize_pages(pages)


def test_snapshot_round_trip_and_corruption(tmp_path):
    payload, version, count = normalize_pages([page()])
    now = datetime(2026, 11, 21, tzinfo=timezone.utc)
    manifest = write_snapshot(tmp_path, payload, version, count, now=now)
    found = load_snapshot(tmp_path, now=now)
    assert found["manifest"] == manifest
    assert found["models"][0]["intelligence"] == "10"
    assert "coding" not in found["models"][0]
    assert found["models"][0]["slug"] == "synthetic-a"
    assert found["models"][0]["name"] == "Synthetic synthetic-a"
    assert found["models"][0]["creator_name"] == "Synthetic"
    csv_file = tmp_path / "snapshots" / manifest["snapshot"]
    csv_file.write_text("tampered", encoding="utf-8")
    assert load_snapshot(tmp_path, now=now) is None


def test_bad_refresh_does_not_replace_valid_snapshot(tmp_path):
    payload, version, count = normalize_pages([page()])
    now = datetime(2026, 11, 21, tzinfo=timezone.utc)
    write_snapshot(tmp_path, payload, version, count, now=now)
    previous = (tmp_path / "manifest.json").read_bytes()
    with pytest.raises(EvidenceError):
        normalize_pages([page(1, 2)])
    assert (tmp_path / "manifest.json").read_bytes() == previous
    assert load_snapshot(tmp_path, now=now)


def test_expired_future_and_symlink_snapshot_are_rejected(tmp_path):
    payload, version, count = normalize_pages([page()])
    now = datetime(2026, 11, 21, tzinfo=timezone.utc)
    write_snapshot(tmp_path, payload, version, count, now=now)
    assert load_snapshot(tmp_path, now=now + timedelta(days=31)) is None
    assert load_snapshot(tmp_path, now=now - timedelta(seconds=1)) is None
    assert load_snapshot(tmp_path, now=now)
    (tmp_path / "manifest.json").unlink()
    (tmp_path / "manifest.json").symlink_to(tmp_path / "snapshots")
    assert load_snapshot(tmp_path, now=now) is None


def test_no_key_and_no_implicit_network():
    with pytest.raises(EvidenceError, match="missing"):
        fetch_free("")
    assert load_snapshot("/definitely/not/an/aa/cache") is None


def test_credential_header_is_not_forwarded_to_another_origin():
    from artificial_analysis import SameOriginRedirectHandler

    request = urllib.request.Request(
        "https://artificialanalysis.ai/api/v2/language/models/free",
        headers={"x-api-key": "not-a-real-secret"},
    )
    with pytest.raises(urllib.error.URLError, match="cross-origin"):
        SameOriginRedirectHandler().redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://attacker.example/record-key",
        )


def test_legacy_v1_snapshot_stays_readable_without_requiring_refresh(tmp_path):
    from artificial_analysis import LEGACY_COLUMNS

    now = datetime(2026, 11, 21, tzinfo=timezone.utc)
    fresh, version, count = normalize_pages([page()])
    manifest = write_snapshot(tmp_path, fresh, version, count, now=now)
    older = (
        ",".join(LEGACY_COLUMNS)
        + "\n"
        + "synthetic-a,synthetic-a,Synthetic,synthetic-vendor,Synthetic,10,25,\n"
    ).encode("utf-8")
    digest = hashlib.sha256(older).hexdigest()
    (tmp_path / "snapshots" / (digest + ".csv")).write_bytes(older)
    manifest.update(
        {"schema_version": 1, "snapshot": digest + ".csv", "sha256": digest}
    )
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert load_snapshot(tmp_path, now=now)["models"][0]["coding"] == "25"


def test_aa_single_page_probe_and_max_page_guard_use_only_one_read():
    calls = []

    def getter(page_number):
        calls.append(page_number)
        return page(page_number, 3)

    assert probe_free("dummy", get_page=getter)["pages"] == 3
    assert calls == [1]
    with pytest.raises(EvidenceError, match="max-pages"):
        fetch_free("dummy", get_page=getter, max_pages=1)
    assert calls == [1, 1]



def test_legacy_v2_intelligence_only_snapshot_remains_readable(tmp_path):
    from artificial_analysis import V2_COLUMNS

    now = datetime(2026, 11, 21, tzinfo=timezone.utc)
    fresh, version, count = normalize_pages([page()])
    manifest = write_snapshot(tmp_path, fresh, version, count, now=now)
    old = (
        ",".join(V2_COLUMNS) + "\n" + "synthetic-a,synthetic-vendor,10\n"
    ).encode("utf-8")
    digest = hashlib.sha256(old).hexdigest()
    (tmp_path / "snapshots" / (digest + ".csv")).write_bytes(old)
    manifest.update({"schema_version": 2, "snapshot": digest + ".csv", "sha256": digest})
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    found = load_snapshot(tmp_path, now=now)
    assert found["models"][0]["intelligence"] == "10"
    assert "slug" not in found["models"][0]
