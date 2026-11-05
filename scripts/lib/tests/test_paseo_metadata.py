"""Local-only Pi metadata model qualification tests."""

import pytest

from paseo_metadata import loopback_url, metadata_changes, resolve_orchestrator


def fake_registry():
    return {
        "preset": "local",
        "presets": {"local": {"orchestrator": {"model": "_local:code-gen"}}},
        "_tiers": {"local": {"profile": {"name": "local"}}},
    }


def test_metadata_manual_list_does_not_override_user_preferences():
    desired = [{"provider": "pi", "model": "omlx/model"}]
    assert metadata_changes({}, "omlx/model") == desired
    assert (
        metadata_changes(
            {"agents": {"metadataGeneration": {"providers": desired}}}, "omlx/model"
        )
        == []
    )
    with pytest.raises(ValueError, match="user-owned"):
        metadata_changes(
            {"agents": {"metadataGeneration": {"providers": [{"provider": "claude"}]}}},
            "omlx/model",
        )


def test_loopback_url_never_accepts_cloud():
    assert loopback_url("http://127.0.0.1:8000/v1")
    assert loopback_url("http://localhost:8765/v1")
    assert not loopback_url("https://api.openai.com/v1")
    assert not loopback_url("http://192.168.1.4:1234/v1")


def test_cloud_preset_refuses_without_discovery():
    reg = fake_registry()
    with pytest.raises(ValueError, match="local model discovery"):
        resolve_orchestrator(reg, "local", [], pi_settings={}, pi_models={})
    reg["presets"]["local"]["orchestrator"]["model"] = "openai/gpt-cloud"
    with pytest.raises(ValueError, match="local"):
        resolve_orchestrator(reg, "local", [], pi_settings={}, pi_models={})


def test_local_orchestrator_must_match_pi_advertised_default(monkeypatch):
    import tier_registry

    monkeypatch.setattr(
        tier_registry,
        "classify_models_for_preset",
        lambda *_args, **_kwargs: {"code-gen": "omlx/mellum"},
    )
    monkeypatch.setattr(
        tier_registry,
        "materialize_role_models",
        lambda *_args: {"orchestrator": "omlx/mellum"},
    )
    registry = fake_registry()
    settings = {"defaultProvider": "omlx", "defaultModel": "mellum"}
    models = {
        "providers": {
            "omlx": {
                "baseUrl": "http://127.0.0.1:8765/v1",
                "models": [{"id": "mellum"}],
            }
        }
    }
    assert (
        resolve_orchestrator(
            registry,
            "local",
            [{"name": "mellum", "provider": "omlx"}],
            pi_settings=settings,
            pi_models=models,
        )
        == "omlx/mellum"
    )
    wrong = {"defaultProvider": "openai", "defaultModel": "gpt"}
    with pytest.raises(ValueError, match="disagrees"):
        resolve_orchestrator(
            registry,
            "local",
            [{"name": "mellum", "provider": "omlx"}],
            pi_settings=wrong,
            pi_models=models,
        )
    models["providers"]["omlx"]["baseUrl"] = "https://api.openai.com/v1"
    with pytest.raises(ValueError, match="loopback"):
        resolve_orchestrator(
            registry,
            "local",
            [{"name": "mellum", "provider": "omlx"}],
            pi_settings=settings,
            pi_models=models,
        )
