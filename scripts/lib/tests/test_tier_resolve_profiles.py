from unittest.mock import patch

import tier_registry
import tier_resolve

FLEET = [
    {"name": "mellum:6b", "provider": "ollama", "param_count": 6},
    {"name": "ornith:35b", "provider": "ollama", "param_count": 35, "is_moe": True},
    {"name": "gemma4:12b", "provider": "ollama", "param_count": 12},
    {"name": "qwen3:32b", "provider": "ollama", "param_count": 32},
]
DETAILS = {
    "mellum:6b": {
        "param_count": 6,
        "capabilities": ["thinking", "completion", "tools"],
        "is_moe": False,
    },
    "ornith:35b": {
        "param_count": 35,
        "capabilities": ["thinking", "completion", "tools"],
        "is_moe": True,
    },
    "gemma4:12b": {
        "param_count": 12,
        "capabilities": ["thinking", "completion", "tools", "vision"],
        "is_moe": False,
    },
    "qwen3:32b": {
        "param_count": 32,
        "capabilities": ["thinking", "completion", "tools"],
        "is_moe": False,
    },
}


def resolve(models, profile=None):
    with patch.object(
        tier_resolve,
        "get_model_details",
        side_effect=lambda name, provider="ollama": DETAILS[name],
    ):
        return tier_resolve.resolve_roles_from_list(models, profile=profile)


def test_profile_policy_decisive_fleet_selections():
    quiet = resolve(FLEET, "quiet")
    balanced = resolve(FLEET, "balanced")
    loud = resolve(FLEET, "loud")
    assert quiet["code-gen"] == "ollama/mellum:6b"
    assert quiet["reasoning"] == "ollama/gemma4:12b"
    assert balanced["code-gen"] == resolve(FLEET)["code-gen"]
    assert loud["code-gen"] == "ollama/ornith:35b"
    assert loud["reasoning"] == "ollama/qwen3:32b"
    assert loud["vision"] == "ollama/gemma4:12b"


def test_mellum_only_quiet_and_balanced_keep_code_gen():
    models = [FLEET[0]]
    quiet = resolve(models, "quiet")
    balanced = resolve(models, "balanced")
    assert quiet == balanced
    assert quiet["code-gen"] == "ollama/mellum:6b"


def test_balanced_is_strict_no_profile_equality():
    assert resolve(FLEET, "balanced") == resolve(FLEET)


def test_category_override_supremacy_for_each_profile():
    registry = {"presets": {"local": {"orchestrator": {"model": "_local:code-gen"}}}}
    for profile in ("quiet", "balanced", "loud"):
        classified = tier_registry.classify_models_for_preset(
            FLEET, registry, "local", profile=profile
        )
        tier_registry.apply_placeholder_overrides(
            classified, ["code-gen=ollama/explicit"]
        )
        assert classified["code-gen"] == "ollama/explicit"
