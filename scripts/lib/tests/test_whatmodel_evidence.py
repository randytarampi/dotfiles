"""Synthetic whatmodel JSON contract and private no-network cache tests."""

from datetime import datetime, timedelta, timezone

import pytest

from model_evidence import load_catalogues
from whatmodel_evidence import (
    WhatmodelError,
    normalize_response,
    store_snapshot,
)


def envelope():
    return {
        "category": "llm",
        "score_mode": "overall",
        "score_set": "all",
        "comparison_set": None,
        "task": None,
        "coverage": "reviewed subset; incomplete coverage",
        "updated": datetime.now(timezone.utc).isoformat(),
        "data_license": {"name": "CC BY-SA 4.0"},
        "models": [
            {
                "id": "synthetic-a",
                "provider": "vendor",
                "score_full": 21.0,
                "n": 6,
                "maker_only": False,
            },
            {
                "id": "synthetic-b",
                "provider": "vendor",
                "score_full": 82.0,
                "n": 12,
                "maker_only": False,
            },
            *[
                {
                    "id": "synthetic-" + str(index),
                    "provider": "vendor",
                    "score_full": 52.0,
                    "n": 6,
                    "maker_only": False,
                }
                for index in range(43)
            ],
        ],
    }


def test_accepts_reviewed_comparable_score_and_preserves_provenance(tmp_path):
    source = normalize_response(envelope())
    assert source["scale"] == "whatmodel-g-v1"
    assert source["licence"] == "CC BY-SA 4.0"
    assert source["metric"] == "overall"
    store_snapshot(source, tmp_path)
    loaded = load_catalogues(tmp_path, aa_evidence={})
    assert len(loaded) == 1
    assert loaded[0]["models"]["synthetic-b"]["score"] == 82


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update({"category": "image"}),
        lambda d: d.update({"score_mode": "set"}),
        lambda d: d.update({"comparison_set": "coding"}),
        lambda d: d.update({"data_license": {"name": "proprietary"}}),
        lambda d: d["models"].append(d["models"][0]),
        lambda d: [m.update({"maker_only": True}) for m in d["models"]],
        lambda d: [m.update({"n": 1}) for m in d["models"]],
        lambda d: [m.update({"score_full": None}) for m in d["models"]],
    ],
)
def test_rejects_incompatible_or_insufficient_response(mutation):
    d = envelope()
    mutation(d)
    with pytest.raises(WhatmodelError):
        normalize_response(d)


def test_bad_refresh_cannot_overwrite_last_good_snapshot(tmp_path):
    source = normalize_response(envelope())
    path = store_snapshot(source, tmp_path)
    before = path.read_bytes()
    bad = envelope()
    bad["score_mode"] = "set"
    with pytest.raises(WhatmodelError):
        normalize_response(bad)
    assert path.read_bytes() == before


def test_stale_snapshot_does_not_rank(tmp_path):
    source = normalize_response(envelope())
    source["observed_at"] = (
        datetime.now(timezone.utc) - timedelta(days=31)
    ).isoformat()
    with pytest.raises(WhatmodelError):
        store_snapshot(source, tmp_path)
    assert load_catalogues(tmp_path, aa_evidence={}) == []


def test_no_data_licence_no_cross_source_identity():
    d = envelope()
    d.pop("data_license")
    with pytest.raises(WhatmodelError):
        normalize_response(d)


def test_reviewed_40_row_subset_is_accepted_with_explicit_coverage(tmp_path):
    payload = envelope()
    payload["models"] = payload["models"][:40]
    snapshot = normalize_response(payload)
    assert snapshot["retrieved_rows"] == 40
    assert snapshot["coverage"] == "reviewed subset; incomplete coverage"
    store_snapshot(snapshot, tmp_path)
    assert len(list(tmp_path.glob("whatmodel.json"))) == 1


def test_exact_source_cap_is_not_claimed_complete():
    payload = envelope()
    seed = payload["models"][-1]
    payload["models"].extend(
        [
            {**seed, "id": "extra-" + str(i)}
            for i in range(2000 - len(payload["models"]))
        ]
    )
    assert normalize_response(payload)["retrieved_rows"] == 2000
    assert (
        normalize_response(payload)["coverage"]
        == "reviewed subset; incomplete coverage"
    )


def test_live_non_json_response_has_clear_client_negotiation_error():
    from io import BytesIO
    from whatmodel_evidence import SOURCE_URL, fetch_response

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, n):
            return BytesIO(b"<html>app shell</html>").read(n)

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == SOURCE_URL
            assert request.get_header("User-agent") == "dotfiles-model-evidence/1"
            return Response()

    with pytest.raises(WhatmodelError, match="non-JSON"):
        fetch_response(opener=Opener())
