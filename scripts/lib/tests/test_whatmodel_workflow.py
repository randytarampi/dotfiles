"""The public catalogue workflow must not probe the retired whatmodel adapter."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/refresh-model-catalogues.yml"


def test_retired_whatmodel_probe_cannot_spend_requests_or_publish():
    contents = WORKFLOW.read_text(encoding="utf-8")
    refresh, publisher = contents.split("  publish:\n", 1)
    assert "whatmodel_probe" not in refresh
    assert "refresh-model-evidence.py" not in refresh
    assert "whatmodel" not in publisher
    assert "actions/upload-artifact@v7" in refresh
    public_artifact = refresh.split("name: Upload the catalogue artifact", 1)[1]
    public_artifact = public_artifact.split("name: Probe Artificial Analysis", 1)[0]
    assert public_artifact.count("free-model-candidates.json") == 1
    assert "gh pr create --draft" in publisher
