import json

from ollama_cloud_models import managed_models, stale_managed_stubs


def test_registry_pull_set_and_stale_cleanup(tmp_path):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"models": {"gemma4:31b": {}, "glm-5.3": {}}}))
    managed = managed_models(registry)
    assert managed == {"gemma4:31b", "glm-5.3"}
    assert stale_managed_stubs(
        ["gemma4:31b:cloud", "glm-5.3-cloud", "retired:cloud", "local:latest"],
        managed,
    ) == ["retired:cloud"]
