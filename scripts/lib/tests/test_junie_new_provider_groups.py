"""Contract tests for explicitly requested Junie Cohere and Poolside groups.

No provider/network credentials are needed. Live tasks remain owner-only.
"""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/generate-jetbrains-profiles.py"
SPEC = importlib.util.spec_from_file_location("junie_provider_groups", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_groups_have_supported_direct_transport_and_permanent_compatibility():
    cfg = json.loads((ROOT / "configs/junie/model-groups.json").read_text())
    expected = {
        "cohere": (
            "https://api.cohere.ai/compatibility/v1",
            "COHERE_API_KEY",
            "command-a-03-2025",
        ),
        "poolside": (
            "https://inference.poolside.ai/v1",
            "POOLSIDE_API_KEY",
            "poolside/laguna-s-2.1",
        ),
    }
    for name, (endpoint, key_env, model) in expected.items():
        assert cfg["providers"][name] == {
            "baseUrl": endpoint,
            "apiType": "OpenAICompletion",
            "apiKeyEnv": key_env,
        }
        assert cfg["groups"][name] == {"provider": name, "primaryModel": model}
        assert name in MODULE.LITELLM_ROUTED_PROVIDERS
    assert cfg["compatibility"] == {
        "openai-gpt-6-luna": "openai-luna",
        "openai-gpt-6.1-sol": "openai-sol",
        "meridian-opus": "anthropic-opus",
        "meridian-sonnet": "anthropic-sonnet",
        "meridian-haiku": "anthropic-haiku",
        "meridian-fable": "anthropic-fable",
    }


def test_twins_are_exact_and_never_auto_direct_fallback():
    groups = json.loads((ROOT / "configs/junie/model-groups.json").read_text())[
        "groups"
    ]
    derived = MODULE.derive_litellm_groups(groups)
    assert not MODULE.routed_group_violations(groups, derived)
    assert derived["litellm-cohere"] == {
        "provider": "litellm",
        "primaryModel": "cohere/command-a-03-2025",
    }
    assert derived["litellm-poolside"] == {
        "provider": "litellm",
        "primaryModel": "poolside/laguna-s-2.1",
    }
    unavailable = MODULE.filter_litellm_groups(derived, set())
    for name in ("litellm-cohere", "litellm-poolside"):
        assert name not in unavailable
    assert groups["cohere"] == MODULE.select_model_groups(groups, False)["cohere"]
    assert "cohere" not in MODULE.select_model_groups(groups, True)


def test_native_poolside_id_and_gateway_alias_are_distinct_contracts():
    # The native /v1/chat/completions call requires the full Poolside ID.
    native = "poolside/laguna-s-2.1"
    assert MODULE.model_id(native, "poolside") == native
    assert MODULE.litellm_alias(native, "poolside") == native
    assert MODULE.litellm_alias("laguna-s-2.1", "poolside") == native


def test_direct_groups_require_provider_keys_but_twins_only_require_junie_key(monkeypatch):
    cfg = json.loads((ROOT / "configs/junie/model-groups.json").read_text())
    monkeypatch.delenv("COHERE_API_KEY", raising=False)
    monkeypatch.delenv("POOLSIDE_API_KEY", raising=False)
    providers = MODULE.build_provider_configs(cfg, "")
    assert "cohere" not in providers
    assert "poolside" not in providers
    monkeypatch.setenv("COHERE_API_KEY", "dummy-cohere")
    monkeypatch.setenv("POOLSIDE_API_KEY", "dummy-poolside")
    with_keys = MODULE.build_provider_configs(cfg, "")
    assert with_keys["cohere"]["apiKey"] == "${COHERE_API_KEY}"
    assert with_keys["poolside"]["apiKey"] == "${POOLSIDE_API_KEY}"
    twins = MODULE.derive_litellm_groups(cfg["groups"])
    assert twins["litellm-poolside"]["primaryModel"] == "poolside/laguna-s-2.1"
