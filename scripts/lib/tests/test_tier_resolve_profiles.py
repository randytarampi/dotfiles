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


def test_loud_council_gamma_uses_second_codegen_candidate():
    models = [
        {"name": "coder-one:7b", "provider": "ollama", "param_count": 7},
        {"name": "ornith:35b", "provider": "ollama", "param_count": 35, "is_moe": True},
        {"name": "mellum:12b", "provider": "ollama", "param_count": 12},
    ]
    details = {
        model["name"]: {
            "param_count": model["param_count"],
            "capabilities": ["thinking", "completion", "tools"],
            "is_moe": model.get("is_moe", False),
        }
        for model in models
    }
    import json
    from pathlib import Path

    slim = json.loads(
        (
            Path(__file__).parents[3] / "configs/opencode/oh-my-opencode-slim.json"
        ).read_text()
    )
    gamma_placeholder = slim["_tiers"]["local-loud"]["council"]["presets"][
        "local-loud"
    ]["gamma"]["model"]
    assert gamma_placeholder == "_local:code-gen_2"
    with patch.object(
        tier_resolve,
        "get_model_details",
        side_effect=lambda name, provider="ollama": details[name],
    ):
        resolved = tier_resolve.resolve_roles_from_list(models, profile="loud")
    gamma = resolved[gamma_placeholder.removeprefix("_local:")]
    assert gamma == "ollama/mellum:12b"
    assert gamma != resolved["code-gen"]


def test_index_three_placeholders_degrade_to_existing_model():
    one_model = [FLEET[0]]
    resolved = resolve(one_model, "loud")
    assert resolved["code-gen_3"] == resolved["code-gen"]
    assert resolved["lightweight_3"] == resolved["lightweight"]

    two_codegen = FLEET[:2]
    resolved = resolve(two_codegen, "loud")
    assert resolved["code-gen_3"] in {resolved["code-gen"], resolved["code-gen_2"]}


def test_loud_council_gamma_resolves_on_small_fleets():
    models = [{"name": "smol-coder:4b", "provider": "ollama", "param_count": 4}]
    details = {
        "smol-coder:4b": {
            "param_count": 4,
            "capabilities": ["completion", "tools"],
            "is_moe": False,
        }
    }
    with patch.object(
        tier_resolve,
        "get_model_details",
        side_effect=lambda name, provider="ollama": details[name],
    ):
        resolved = tier_resolve.resolve_roles_from_list(models, profile="loud")

    assert resolved["code-gen_2"] == resolved["code-gen"]
    assert resolved["code-gen_2"] == "ollama/smol-coder:4b"


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


def resolve_reasoning_admission(gemma_capabilities, gemma_embedding):
    models = [
        {"name": "mellum:6b", "provider": "ollama", "param_count": 6},
        {"name": "gemma4:12b", "provider": "ollama", "param_count": 12},
    ]
    details = {
        "mellum:6b": {
            "param_count": 6,
            "capabilities": ["thinking", "completion", "tools"],
            "embedding_length": 4096,
            "is_moe": False,
        },
        "gemma4:12b": {
            "param_count": 12,
            "capabilities": gemma_capabilities,
            "embedding_length": gemma_embedding,
            "is_moe": False,
        },
    }
    with patch.object(
        tier_resolve,
        "get_model_details",
        side_effect=lambda name, provider="ollama": details[name],
    ):
        return tier_resolve.resolve_roles_from_list(
            models, min_reasoning_embedding=4096, profile="quiet"
        )


def test_quiet_keeps_reasoning_candidate_when_gemma_lacks_thinking():
    resolved = resolve_reasoning_admission(["completion", "tools"], 2048)

    assert resolved["reasoning"] == "ollama/mellum:6b"


def test_quiet_keeps_reasoning_candidate_when_gemma_embedding_is_too_small():
    resolved = resolve_reasoning_admission(["thinking", "tools"], 2048)

    assert resolved["reasoning"] == "ollama/mellum:6b"


def test_quiet_substitutes_qualified_gemma_reasoning_candidate():
    resolved = resolve_reasoning_admission(["thinking", "tools"], 4096)

    assert resolved["reasoning"] == "ollama/gemma4:12b"


def test_quiet_gemma_substitution_searches_all_qualified_candidates():
    models = [
        {"name": "mellum:6b", "provider": "ollama", "param_count": 6},
        {"name": "gemma4:31b", "provider": "ollama", "param_count": 31},
        {"name": "gemma4:12b", "provider": "ollama", "param_count": 12},
    ]
    details = {
        "mellum:6b": {
            "param_count": 6,
            "capabilities": ["thinking", "completion", "tools"],
            "embedding_length": 4096,
            "is_moe": False,
        },
        "gemma4:31b": {
            "param_count": 31,
            "capabilities": ["completion", "vision", "tools"],
            "embedding_length": 4096,
            "is_moe": False,
        },
        "gemma4:12b": {
            "param_count": 12,
            "capabilities": ["thinking", "completion", "tools", "vision"],
            "embedding_length": 4096,
            "is_moe": False,
        },
    }
    with patch.object(
        tier_resolve,
        "get_model_details",
        side_effect=lambda name, provider="ollama": details[name],
    ):
        resolved = tier_resolve.resolve_roles_from_list(
            models, min_reasoning_embedding=4096, profile="quiet"
        )

    assert resolved["reasoning"] == "ollama/gemma4:12b"


def test_omlx_status_moe_metadata_beats_name_pattern():
    engine = {
        "metadata_lookup": lambda name: {"is_moe": True},
        "metadata_capabilities": lambda status: [],
    }
    with patch.object(tier_resolve, "resolve_engine", return_value=engine):
        details = tier_resolve.get_model_details(
            "dense-named-model-9b", provider="omlx"
        )

    assert details["is_moe"] is True


def test_loud_ranking_preserves_source_moe_metadata():
    models = [
        {"name": "moe-coder:9b", "provider": "omlx", "is_moe": True, "param_count": 9},
        {
            "name": "dense-coder:30b",
            "provider": "omlx",
            "is_moe": False,
            "param_count": 30,
        },
    ]
    failing_details = {
        "param_count": None,
        "capabilities": [],
        "architecture": None,
        "embedding_length": None,
        "context_length": None,
        "quantization": None,
        "is_moe": False,
    }
    with patch.object(tier_resolve, "get_model_details", return_value=failing_details):
        resolved = tier_resolve.resolve_roles_from_list(models, profile="loud")

    assert resolved["code-gen"] == "omlx/moe-coder:9b"


def test_promoted_reasoning_entry_carries_source_moe_metadata():
    models = [
        {
            "name": "moe-coder:90b",
            "provider": "omlx",
            "is_moe": True,
            "param_count": 90,
        },
        {
            "name": "dense-coder:30b",
            "provider": "omlx",
            "is_moe": False,
            "param_count": 30,
        },
    ]
    details = {
        # Detail lookup succeeds on capabilities (so the MoE model is
        # promoted to reasoning) but cannot establish MoE status.
        "moe-coder:90b": {
            "param_count": None,
            "capabilities": ["thinking", "completion", "tools"],
            "is_moe": False,
        },
        "dense-coder:30b": {
            "param_count": None,
            "capabilities": ["thinking", "completion", "tools"],
            "is_moe": False,
        },
    }
    with patch.object(
        tier_resolve,
        "get_model_details",
        side_effect=lambda name, provider="ollama": details[name],
    ):
        resolved = tier_resolve.resolve_roles_from_list(models, profile="balanced")

    # The promoted MoE entry must not masquerade as dense in the density
    # sort merely because the detail lookup missed its MoE status.
    assert resolved["reasoning"] == "omlx/dense-coder:30b"


def test_quiet_gemma_selection_sizes_by_name_when_details_omit_counts():
    models = [
        {"name": "mellum:6b", "provider": "ollama", "param_count": 6},
        {"name": "gemma4-14b-noshow", "provider": "ollama", "param_count": None},
        {"name": "gemma4:8b", "provider": "ollama", "param_count": 8},
    ]
    details = {
        "mellum:6b": {
            "param_count": 6,
            "capabilities": ["thinking", "completion", "tools"],
            "is_moe": False,
        },
        "gemma4-14b-noshow": {
            "param_count": None,
            "capabilities": ["thinking", "completion", "tools"],
            "is_moe": False,
        },
        "gemma4:8b": {
            "param_count": 8,
            "capabilities": ["thinking", "completion", "tools"],
            "is_moe": False,
        },
    }
    with patch.object(
        tier_resolve,
        "get_model_details",
        side_effect=lambda name, provider="ollama": details[name],
    ):
        resolved = tier_resolve.resolve_roles_from_list(
            models, min_reasoning_embedding=0, profile="quiet"
        )

    # Size must come from the name tag when the detail lookup omits the
    # count — an unknown-size Gemma must not outrank a known smaller one.
    assert resolved["reasoning"] == "ollama/gemma4:8b"
