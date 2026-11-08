"""Nightly gate-report exit semantics: DEGRADED must fail the report job.

Owner decision 2026-11-08 (issue #212): the failed nightly run itself is the
notification mechanism, so the reporter exits non-zero whenever the aggregate
is not PASS, after the summary and JSON are written. Gate jobs keep
continue-on-error; receipts and artifacts must still publish on the red run.
"""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / ".github/scripts/nightly_gate_report.py"
SPEC = importlib.util.spec_from_file_location("nightly_gate_report", SCRIPT)
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)

GATES = (
    "DOTFILES_RUN_OPENCODE_SETUP",
    "DOTFILES_RUN_MCP_SETUP",
    "DOTFILES_RUN_AGENT_GUIDANCE_SETUP",
)


def write_receipts(directory, statuses):
    directory.mkdir(parents=True, exist_ok=True)
    for gate, status in zip(GATES, statuses):
        path = directory / f"{gate}.txt"
        if status is None:
            continue
        path.write_text(f"gate={gate}\nresult={status}\n", encoding="utf-8")


@pytest.fixture
def reporter(tmp_path):
    results = tmp_path / "nightly-results"
    summary = tmp_path / "summary.md"
    json_path = tmp_path / "nightly-gate-report.json"
    return results, summary, json_path


def run_reporter(paths):
    results, summary, json_path = paths
    return report.main(
        ["--results", str(results), "--summary", str(summary), "--json", str(json_path)]
    )


def test_pass_aggregate_exits_zero(reporter):
    write_receipts(reporter[0], ["success", "success", "success"])
    assert run_reporter(reporter) == 0
    assert json.loads(reporter[2].read_text(encoding="utf-8"))["overall"] == "PASS"


def test_failed_gate_exits_one_after_summary_and_json_are_written(reporter):
    results, summary, json_path = reporter
    write_receipts(results, ["success", "failure", "success"])
    assert run_reporter(reporter) == 1
    aggregate = json.loads(json_path.read_text(encoding="utf-8"))
    assert aggregate["overall"] == "DEGRADED"
    assert aggregate["gates"]["DOTFILES_RUN_MCP_SETUP"] == "failure"
    assert "DOTFILES_RUN_MCP_SETUP" in summary.read_text(encoding="utf-8")


def test_missing_receipt_degrades_and_exits_one(reporter):
    results, _, json_path = reporter
    write_receipts(results, ["success", "success", None])
    assert run_reporter(reporter) == 1
    aggregate = json.loads(json_path.read_text(encoding="utf-8"))
    assert aggregate["overall"] == "DEGRADED"
    assert aggregate["gates"][GATES[2]] == "MISSING"
    assert aggregate["errors"] == []


def test_malformed_receipt_never_fakes_pass(reporter):
    results, _, json_path = reporter
    write_receipts(results, ["success", "success", "success"])
    (results / f"{GATES[1]}.txt").write_text(
        "gate=nope\nresult=success\n", encoding="utf-8"
    )
    assert run_reporter(reporter) == 1
    aggregate = json.loads(json_path.read_text(encoding="utf-8"))
    assert aggregate["overall"] == "DEGRADED"
    assert aggregate["errors"]


def test_absent_results_directory_still_fails_and_writes_reports(reporter):
    _, summary, json_path = reporter
    assert run_reporter(reporter) == 1
    aggregate = json.loads(json_path.read_text(encoding="utf-8"))
    assert aggregate["overall"] == "DEGRADED"
    assert set(aggregate["gates"].values()) == {"MISSING"}
    assert "DEGRADED" in summary.read_text(encoding="utf-8")


def test_unknown_gate_and_invalid_status_fail_closed(reporter):
    results, _, json_path = reporter
    write_receipts(results, ["success", "success", "success"])
    (results / "unexpected.txt").write_text(
        "gate=NOT_A_REGISTERED_GATE\nresult=success\n", encoding="utf-8"
    )
    (results / f"{GATES[0]}.txt").write_text(
        f"gate={GATES[0]}\nresult=unknown\n", encoding="utf-8"
    )
    assert run_reporter(reporter) == 1
    aggregate = json.loads(json_path.read_text(encoding="utf-8"))
    assert aggregate["overall"] == "DEGRADED"
    assert aggregate["gates"][GATES[0]] == "MISSING"
    assert "invalid gate result" in aggregate["errors"]
    assert "unexpected gate receipt" in aggregate["errors"]


@pytest.mark.parametrize(
    ("receipt_text", "expected_error"),
    [
        (
            "gate=DOTFILES_RUN_MCP_SETUP\nresult=success\nextra=ignored\n",
            "invalid receipt field",
        ),
        (
            "gate=DOTFILES_RUN_MCP_SETUP\nresult=success\nresult=success\n",
            "invalid receipt field",
        ),
        ("gate=DOTFILES_RUN_MCP_SETUP\nresult\n", "malformed receipt"),
    ],
)
def test_receipt_schema_errors_never_pass(reporter, receipt_text, expected_error):
    results, _, json_path = reporter
    write_receipts(results, ["success", "success", "success"])
    (results / f"{GATES[1]}.txt").write_text(receipt_text, encoding="utf-8")
    assert run_reporter(reporter) == 1
    aggregate = json.loads(json_path.read_text(encoding="utf-8"))
    assert aggregate["overall"] == "DEGRADED"
    assert aggregate["gates"][GATES[1]] == "MISSING"
    assert expected_error in aggregate["errors"]


def test_unreadable_utf8_receipt_still_fails_closed(reporter):
    results, _, json_path = reporter
    write_receipts(results, ["success", "success", "success"])
    (results / f"{GATES[0]}.txt").write_bytes(b"\xff")
    assert run_reporter(reporter) == 1
    aggregate = json.loads(json_path.read_text(encoding="utf-8"))
    assert aggregate["overall"] == "DEGRADED"
    assert "unreadable receipt" in aggregate["errors"]


@pytest.mark.parametrize("status", ["cancelled", "skipped"])
def test_nonpassing_gate_emits_warning_and_preserves_artifacts(reporter, capsys, status):
    results, summary, json_path = reporter
    write_receipts(results, ["success", status, "success"])
    assert run_reporter(reporter) == 1
    assert "DEGRADED" in summary.read_text(encoding="utf-8")
    aggregate = json.loads(json_path.read_text(encoding="utf-8"))
    assert aggregate["overall"] == "DEGRADED"
    assert aggregate["gates"][GATES[1]] == status
    assert "::warning title=Nightly integration gates degraded::" in capsys.readouterr().out
