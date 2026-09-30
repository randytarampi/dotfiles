import json

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
