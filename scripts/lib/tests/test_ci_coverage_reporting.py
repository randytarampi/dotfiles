"""Keep external coverage uploads outside the required test/verify gate.

The coverallsapp action installs its uploader from a Homebrew tap on macOS.
A network/installer failure must not turn successful tests into a failed
ci/required status, and must not prevent the independent Qlty upload.
"""

from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[3] / ".github/workflows/ci.yml"


def _step(name):
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = lines.index(f"      - name: {name}")
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].startswith("      - name: ")
            or lines[index].startswith("  deploy:")
        ),
        len(lines),
    )
    return "\n".join(lines[start:end])


def test_coveralls_installer_failure_is_warned_but_does_not_fail_tests():
    upload = _step("Upload coverage")
    assert "id: coveralls" in upload
    assert "continue-on-error: true" in upload
    assert "uses: coverallsapp/github-action@v2" in upload
    assert "file: coverage.lcov" in upload
    assert "parallel: true" in upload

    warning = _step("Report Coveralls upload failure")
    assert "if: ${{ steps.coveralls.outcome == 'failure' }}" in warning
    assert "::warning::" in warning


def test_independent_qlty_upload_still_runs_after_handled_coveralls_failure():
    qlty = _step("Upload coverage to Qlty")
    assert "if: ${{ success() }}" in qlty
    assert "continue-on-error: true" in qlty
    assert "uses: qltysh/qlty-action/coverage@v2" in qlty


def test_coverage_tolerance_does_not_hide_real_test_failures():
    for name in ("CI verification", "Python tests", "Shell tests"):
        assert "continue-on-error" not in _step(name)
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "ci/required" in workflow
