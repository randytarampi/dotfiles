"""Producer cache contract: type safety, reserved names and bundle identity."""

import json
from datetime import datetime, timezone

import pytest

from artificial_analysis import load_snapshot
from model_evidence import _normalize_source, load_catalogues

NOW = datetime(2026, 11, 21, tzinfo=timezone.utc)
SURFACES = ("reasoning", "coding", "agentic", "overall")


def source(**overrides):
    payload = {
        "schema_version": 1,
        "source": "benchlm-coding",
        "metric": "coding",
        "scale": "benchalign-v5-coding",
        "observed_at": "2026-11-21T00:00:00+00:00",
        "models": [
            {"id": "a", "creator_id": "maker", "score": 10, "evidence": "mixed"},
            {"id": "b", "creator_id": "maker", "score": 20, "evidence": "mixed"},
        ],
        "bundle": {"id": "gen-1", "members": ["benchlm-coding"]},
        "coverage": "complete",
        "retrieved_rows": 2,
        "population_total": 2,
    }
    payload.update(overrides)
    return payload


def test_non_object_manifests_are_unavailable(tmp_path):
    for index, text in enumerate(("[]", "null", '"text"')):
        directory = tmp_path / str(index)
        directory.mkdir()
        (directory / "manifest.json").write_text(text, encoding="utf-8")
        assert load_snapshot(directory) is None


def test_unhashable_fields_are_rejected_not_raised():
    for field, value in (("metric", []), ("metric", {}), ("scale", ["x"])):
        assert (
            _normalize_source(source(**{field: value}), "benchlm-coding", now=NOW)
            is None
        )
    unhashable_evidence = {
        "id": "a",
        "creator_id": "maker",
        "score": 10,
        "evidence": [],
    }
    assert (
        _normalize_source(
            source(models=[unhashable_evidence]), "benchlm-coding", now=NOW
        )
        is None
    )
    assert _normalize_source(source(coverage=[]), "benchlm-coding", now=NOW) is None


def test_generic_file_cannot_spoof_the_builtin_aa_source(tmp_path):
    payload = source(source="artificial-analysis", metric="intelligence")
    (tmp_path / "artificial-analysis.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    assert load_catalogues(tmp_path, aa_evidence={}, now=NOW) == []


def test_singleton_benchlm_bundle_is_not_a_source(tmp_path):
    (tmp_path / "benchlm-coding.json").write_text(
        json.dumps(source()), encoding="utf-8"
    )
    assert load_catalogues(tmp_path, aa_evidence={}, now=NOW) == []


def test_manifest_generation_is_resolved(tmp_path):
    members = sorted("benchlm-" + surface for surface in SURFACES)
    root = tmp_path / "generations" / "gen-1"
    root.mkdir(parents=True)
    for surface in SURFACES:
        payload = source(
            source="benchlm-" + surface,
            metric=surface,
            scale="benchalign-v5-" + surface,
            bundle={"id": "gen-1", "members": members},
            models=[
                {
                    "id": surface + "-a",
                    "creator_id": "maker",
                    "score": 10,
                    "evidence": "mixed",
                },
                {
                    "id": surface + "-b",
                    "creator_id": "maker",
                    "score": 20,
                    "evidence": "mixed",
                },
            ],
        )
        (root / ("benchlm-" + surface + ".json")).write_text(
            json.dumps(payload), encoding="utf-8"
        )
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generation": "gen-1",
                "bundle": {"id": "gen-1", "members": members},
                "sources": ["benchlm-" + surface + ".json" for surface in SURFACES],
            }
        ),
        encoding="utf-8",
    )
    loaded = load_catalogues(tmp_path, aa_evidence={}, now=NOW)
    assert {row["source"] for row in loaded} == set(members)


def test_unusable_manifest_hides_flat_files(tmp_path):
    # A present-but-broken manifest must not silently fall back to a stale
    # flat layout that the producer no longer updates.
    (tmp_path / "benchlm-coding.json").write_text(
        json.dumps(source()), encoding="utf-8"
    )
    (tmp_path / "manifest.json").write_text("[]", encoding="utf-8")
    assert load_catalogues(tmp_path, aa_evidence={}, now=NOW) == []


def _mutated(change):
    payload = json.loads(json.dumps(source()))
    change(payload)
    return payload


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.update(schema_version=2),
        lambda p: p.update(source="other"),
        lambda p: p.update(metric="nope"),
        lambda p: p.update(scale=""),
        lambda p: p.update(observed_at=123),
        lambda p: p.update(observed_at="2026-11-21"),
        lambda p: p.update(models="x"),
        lambda p: p.update(models=["x"]),
        lambda p: p["models"][0].update(id=""),
        lambda p: p["models"][0].update(score="x"),
        lambda p: p.update(coverage="partial"),
        lambda p: p.update(retrieved_rows=1),
        lambda p: p.update(retrieved_rows=101, coverage="first-100-subset"),
        lambda p: p.update(bundle="x"),
        lambda p: p["bundle"].update(id=""),
        lambda p: p["bundle"].update(members="x"),
        lambda p: p["bundle"].update(members=["benchlm-coding", "benchlm-coding"]),
        lambda p: p["bundle"].update(members=["other"]),
    ],
)
def test_normalize_source_rejects_invalid_payloads(change):
    payload = _mutated(change)
    assert _normalize_source(payload, "benchlm-coding", now=NOW) is None


def test_normalize_source_rejects_duplicate_ids_and_old_data():
    duplicate = _mutated(lambda p: p["models"].append(dict(p["models"][0])))
    assert _normalize_source(duplicate, "benchlm-coding", now=NOW) is None
    old = _mutated(lambda p: p.update(observed_at="2026-10-01T00:00:00+00:00"))
    assert _normalize_source(old, "benchlm-coding", now=NOW) is None


def test_load_catalogues_skips_symlink_and_oversized_files(tmp_path):
    (tmp_path / "custom.json").symlink_to(tmp_path / "missing.json")
    (tmp_path / "big.json").write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    (tmp_path / "bad.json").write_bytes(b"[]")
    assert load_catalogues(tmp_path, aa_evidence={}, now=NOW) == []


def test_load_catalogues_reads_a_valid_artificial_analysis_snapshot(tmp_path):
    aa = {
        "manifest": {"index_version": "5", "observed_at": "2026-11-21T00:00:00Z"},
        "models": [{"id": "m", "creator_id": "c", "intelligence": "70"}],
    }
    loaded = load_catalogues(tmp_path, aa_evidence=aa, now=NOW)
    assert [row["source"] for row in loaded] == ["artificial-analysis"]
    assert loaded[0]["scale"] == "aa-intelligence-5"


def test_unreviewed_or_sparse_rows_are_skipped_not_fatal():
    for change in (
        lambda p: p["models"][0].update(evidence="official"),
        lambda p: p["models"][0].update(n=2),
    ):
        payload = _mutated(change)
        normalized = _normalize_source(payload, "benchlm-coding", now=NOW)
        assert normalized is not None
        assert set(normalized["models"]) == {"b"}
