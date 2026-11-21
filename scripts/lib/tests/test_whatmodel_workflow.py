"""No-key whatmodel probe must not weaken public Woofbot publication."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/refresh-model-catalogues.yml"


def test_whatmodel_is_only_an_optional_private_quality_probe():
    contents = WORKFLOW.read_text(encoding="utf-8")
    refresh, publisher = contents.split("  publish:\n", 1)
    assert "Probe public whatmodel evidence (advisory)" in refresh
    assert "continue-on-error: true" in refresh
    assert "steps.whatmodel_probe.outcome" in refresh
    assert "scripts/refresh-model-evidence.py --refresh" in refresh
    assert "scripts/refresh-model-evidence.py --offline" in refresh
    assert "secrets.ARTIFICIAL_ANALYSIS_API_KEY" not in publisher
    assert "whatmodel" not in publisher
    assert "actions/upload-artifact@v7" in refresh
    public_artifact = refresh.split("name: Upload the catalogue artifact", 1)[1]
    public_artifact = public_artifact.split("name: Probe Artificial Analysis", 1)[0]
    assert public_artifact.count("free-model-candidates.json") == 1
    assert "gh pr create --draft" in publisher
