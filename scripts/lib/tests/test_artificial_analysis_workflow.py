"""AA advisory probe must not modify the protected Woofbot publisher."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/refresh-model-catalogues.yml"


def test_aa_probe_is_private_advisory_only():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    refresh, publish = workflow.split("  publish:\n", 1)
    assert (
        "ARTIFICIAL_ANALYSIS_API_KEY: ${{ secrets.ARTIFICIAL_ANALYSIS_API_KEY }}"
        in refresh
    )
    assert "ARTIFICIAL_ANALYSIS_API_KEY" not in publish
    assert "Probe Artificial Analysis Free API evidence (advisory)" in refresh
    assert "continue-on-error: true" in refresh
    assert (
        "python3 scripts/refresh-artificial-analysis.py --probe"
    ) in refresh
    assert (
        "status=schema_validated"
    ) in refresh
    artifact_step = refresh.split("      - name: Upload the catalogue artifact\n", 1)[1]
    artifact_step = artifact_step.split(
        "      - name: Probe Artificial Analysis Free API evidence", 1
    )[0]
    assert (
        artifact_step.count("artifacts/model-catalogues/free-model-candidates.json")
        == 1
    )
    assert "artificial-analysis" not in artifact_step
    assert (
        'allowed_path="artifacts/model-catalogues/free-model-candidates.json"'
        in publish
    )
    assert (
        '[[ "${published_paths[0]}" == "artifacts/model-catalogues/free-model-candidates.json" ]]'
        in publish
    )
    assert "gh pr create --draft" in publish
