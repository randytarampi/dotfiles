import json
from pathlib import Path

from ollama_cloud_models import managed_models, stale_managed_stubs


def test_registry_pull_set_and_stale_cleanup(tmp_path):
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps({"models": {"fixture-model-a": {}, "fixture-model-b": {}}})
    )
    managed = managed_models(registry)
    assert managed == {"fixture-model-a", "fixture-model-b"}
    assert stale_managed_stubs(
        [
            "fixture-model-a:cloud",
            "fixture-model-b-cloud",
            "retired:cloud",
            "local:latest",
        ],
        managed,
    ) == ["retired:cloud"]

def test_chat_registry_excludes_system_one_only_decision_models():
    # This registry feeds OpenCode/Pi chat completions; Clef requires /v1/systemone.
    registry = (
        Path(__file__).resolve().parents[3]
        / "configs/opencode/ollama-cloud-models.json"
    )
    assert "clef" not in managed_models(registry)
