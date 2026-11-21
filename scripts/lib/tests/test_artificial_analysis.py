"""Synthetic, licence-safe Artificial Analysis Free API regression tests."""

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from artificial_analysis import (
    EvidenceError,
    fetch_free,
    load_snapshot,
    normalize_pages,
    write_snapshot,
)


def page(number=1, total=1, *, model_id="synthetic-a", coding=25, version=4.1):
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
                    "artificial_analysis_intelligence_index": 10,
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
    assert b"agentic" in payload


@pytest.mark.parametrize(
    "pages",
    [
        [page(1, 2)],
        [page(2, 2)],
        [page(1, 2), page(2, 2, model_id="synthetic-a")],
        [page(1, 2), page(2, 2, version=5.0, model_id="synthetic-b")],
        [page(1, 1, coding=float("nan"))],
        [page(1, 1, coding=True)],
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
    assert found["models"][0]["coding"] == "25"
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
