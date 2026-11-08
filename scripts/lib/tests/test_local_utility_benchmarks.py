"""Offline, no-promotion utility model benchmark aggregation tests."""

import importlib.util
from pathlib import Path
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "summarize-local-utility-benchmarks.py"
SPEC = importlib.util.spec_from_file_location("utility_benchmarks", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def row(**changes):
    result = dict(
        device="m5-pro-48gb",
        model="local/candidate",
        task="metadata-title",
        latency_ms=120,
        peak_gb=3.5,
        passed=True,
        network_egress=False,
        provenance="owner-verified",
    )
    result.update(changes)
    return result


def test_summary_preserves_evidence_and_does_not_promote():
    result = MODULE.summarize([row(), row(latency_ms=220, passed=False)])
    assert len(result) == 1
    summary = result[0]
    assert summary["count"] == 2
    assert summary["median_latency_ms"] == 170
    assert summary["all_passed"] is False
    assert summary["admission"] == "NOT_AUTO_PROMOTED"
    assert summary["any_network_egress"] is False


def test_egress_and_provenance_never_hide_unknown():
    summary = MODULE.summarize(
        [
            row(network_egress=True, provenance="synthetic"),
            row(provenance="unverified"),
        ]
    )
    assert len(summary) == 2  # Unverified samples never affect synthetic medians.
    assert {item["provenance"][0] for item in summary} == {
        "synthetic",
        "unverified",
    }
    assert any(item["any_network_egress"] for item in summary)


@pytest.mark.parametrize(
    "changes",
    [
        {"latency_ms": float("nan")},
        {"latency_ms": -1},
        {"peak_gb": True},
        {"passed": "true"},
        {"provenance": "invented"},
        {"prompt": "private user input"},
        {"model": ""},
        {"model": "good\\nsecret"},
        {"task": "test prompt data"},
        {"device": "x" * 150},
    ],
)
def test_bad_or_sensitive_records_are_rejected(changes):
    with pytest.raises(ValueError):
        MODULE.summarize([row(**changes)])


def test_empty_input_produces_no_claim():
    assert MODULE.summarize([]) == []


def test_disjoint_evidence_medians_cannot_mix():
    rows = [
        row(provenance="synthetic", latency_ms=1),
        row(provenance="owner-verified", latency_ms=1000),
    ]
    report = MODULE.summarize(rows)
    assert len(report) == 2
    assert {item["median_latency_ms"] for item in report} == {1, 1000}


def test_local_input_is_bounded_and_symlinks_refused(tmp_path):
    path = tmp_path / "results.json"
    path.write_text("[]" + " " * MODULE.MAX_INPUT_BYTES)
    with pytest.raises(ValueError, match="limit"):
        MODULE.load_results(path)
    path.write_text("[]")
    assert MODULE.load_results(path) == []
    link = tmp_path / "alias.json"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="nonsymlink"):
        MODULE.load_results(link)
