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


def test_final_coveralls_publisher_does_not_gate_required_ci():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    finaliser = workflow.split("  finish:", 1)[1].split("  ci-required:", 1)[0]
    required = workflow.split("  ci-required:", 1)[1]

    assert "needs: verify" in finaliser
    assert "uses: actions/checkout@v7" in finaliser
    assert "persist-credentials: false" in finaliser
    assert "parallel-finished: true" in finaliser
    assert "continue-on-error: true" in finaliser
    assert "uses: coverallsapp/github-action@v2" in finaliser

    assert "name: ci/required" in required
    # Source/deployment AND the core smoke matrix all gate the same stable
    # status; advisory coverage finalisation must never affect it.
    assert "needs: [verify, deploy, bootstrap-smoke, bootstrap-networkless]" in required
    for dependency in ("verify", "deploy", "bootstrap-smoke", "bootstrap-networkless"):
        assert f"needs.{dependency}.result" in required
    assert "needs.finish.result" not in required
    assert "FINISH_RESULT" not in required
    assert (
        'for result in "$VERIFY_RESULT" "$DEPLOY_RESULT" "$SMOKE_RESULT" "$NETWORK_RESULT"; do'
        in required
    )
    assert '"$result" != "success"' in required
