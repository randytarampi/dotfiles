"""Free BenchLM secret/data confinement in the trusted catalogue workflow."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/refresh-model-catalogues.yml"


def test_benchlm_owner_only_probe_does_not_enter_publisher():
    content = WORKFLOW.read_text(encoding="utf-8")
    refresh, publisher = content.split("  publish:\n", 1)
    assert "Probe BenchLM Free current rankings (owner-only advisory)" in refresh
    probe = refresh.split("      - name: Probe BenchLM Free", 1)[1]
    assert "github.event_name == 'workflow_dispatch'" in probe
    assert "github.actor == github.repository_owner" in probe
    assert "BENCHLM_DATA_KEY: ${{ secrets.BENCHLM_DATA_KEY }}" in probe
    assert "continue-on-error: true" in probe
    assert "status=skipped_no_key" in probe
    assert "scripts/refresh-benchlm-evidence.py --refresh" in probe
    assert "scripts/refresh-benchlm-evidence.py --offline" in probe
    assert "BENCHLM_DATA_KEY" not in publisher
    assert "benchlm" not in publisher.lower()
    artifact = refresh.split("      - name: Upload the catalogue artifact", 1)[1]
    artifact = artifact.split("      - name: Probe Artificial Analysis", 1)[0]
    assert artifact.count("artifacts/model-catalogues/free-model-candidates.json") == 1
    assert (
        'allowed_path="artifacts/model-catalogues/free-model-candidates.json"'
        in publisher
    )
