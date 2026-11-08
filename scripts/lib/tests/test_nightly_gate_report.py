"""Regression tests for the honest scheduled-gate result reporter."""

from pathlib import Path
import importlib.util

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / ".github/scripts/nightly_gate_report.py"
spec = importlib.util.spec_from_file_location("nightly_gate_report", SCRIPT)
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def receipt(path, gate, status):
    path.write_text(f"gate={gate}\nresult={status}\n", encoding="utf-8")


def test_complete_success_is_pass(tmp_path):
    for gate in report.GATES:
        receipt(tmp_path / f"{gate}.txt", gate, "success")
    result = report.summarize(tmp_path)
    assert result["overall"] == "PASS"
    assert all(value == "success" for value in result["gates"].values())
    assert not result["errors"]


def test_failed_gate_remains_visible_despite_allow_failure(tmp_path):
    for gate in report.GATES:
        receipt(tmp_path / f"{gate}.txt", gate, "success")
    receipt(tmp_path / f"{report.GATES[1]}.txt", report.GATES[1], "failure")
    result = report.summarize(tmp_path)
    assert result["overall"] == "DEGRADED"
    assert result["gates"][report.GATES[1]] == "failure"
    assert "failure" in report.markdown(result)


def test_missing_artifact_and_empty_download_are_not_success(tmp_path):
    gate = report.GATES[0]
    receipt(tmp_path / f"{gate}.txt", gate, "success")
    result = report.summarize(tmp_path)
    assert result["overall"] == "DEGRADED"
    assert set(result["gates"].values()) == {"success", "MISSING"}
    assert report.summarize(tmp_path / "absent")["overall"] == "DEGRADED"


def test_corrupt_or_unknown_receipt_fails_closed(tmp_path):
    receipt(tmp_path / "unexpected.txt", "NOT_A_REGISTERED_GATE", "success")
    receipt(tmp_path / f"{report.GATES[0]}.txt", report.GATES[0], "unknown")
    result = report.summarize(tmp_path)
    assert result["overall"] == "DEGRADED"
    assert "invalid gate result" in result["errors"]
    assert "unexpected gate receipt" in result["errors"]


@pytest.mark.parametrize(
    "content",
    [
        "gate=DOTFILES_RUN_MCP_SETUP\nresult=success\nextra=ignored\n",
        "gate=DOTFILES_RUN_MCP_SETUP\nresult=success\nresult=success\n",
        "gate=DOTFILES_RUN_MCP_SETUP\nresult\n",
    ],
)
def test_receipt_schema_error_never_passes(tmp_path, content):
    gate = report.GATES[1]
    for expected in report.GATES:
        receipt(tmp_path / f"{expected}.txt", expected, "success")
    (tmp_path / f"{gate}.txt").write_text(content, encoding="utf-8")
    result = report.summarize(tmp_path)
    assert result["overall"] == "DEGRADED"
    assert result["errors"]
    assert result["gates"][gate] == "MISSING"


def test_invalid_utf8_receipt_is_reported_as_degraded(tmp_path):
    for gate in report.GATES:
        receipt(tmp_path / f"{gate}.txt", gate, "success")
    (tmp_path / f"{report.GATES[0]}.txt").write_bytes(b"\xff")
    result = report.summarize(tmp_path)
    assert result["overall"] == "DEGRADED"
    assert "unreadable receipt" in result["errors"]


def test_report_cli_writes_machine_and_human_repair_artifacts(tmp_path, capsys):
    receipt(tmp_path / f"{report.GATES[0]}.txt", report.GATES[0], "cancelled")
    summary, json_file = tmp_path / "summary.md", tmp_path / "result.json"
    assert (
        report.main(
            [
                "--results",
                str(tmp_path),
                "--summary",
                str(summary),
                "--json",
                str(json_file),
            ]
        )
        == 0
    )
    assert "DEGRADED" in summary.read_text()
    assert '"overall": "DEGRADED"' in json_file.read_text()
    assert (
        "::warning title=Nightly integration gates degraded::"
        in capsys.readouterr().out
    )
