"""Offline mapping-onboarding review: suggestions never authorize a score."""

import json
from unittest.mock import patch

import tier_resolve
from model_evidence_onboarding import build_suggestions


def model(name, provider="omlx"):
    return {"name": name, "provider": provider, "capabilities": ["tools", "thinking"]}


def test_aa_uuid_source_can_be_suggested_by_restored_private_display_keys(tmp_path):
    source = {
        "source": "artificial-analysis",
        "metric": "intelligence",
        "scale": "aa-intelligence-5",
        "models": {"opaque-uuid": {"creator_id": "creator-uuid", "score": 79}},
    }
    aa = {
        "models": [
            {
                "id": "opaque-uuid",
                "slug": "qwen3.8-27b",
                "name": "Qwen3.8-27B",
                "creator_id": "creator-uuid",
                "creator_name": "Qwen",
                "intelligence": "79",
            }
        ]
    }
    category = {"reasoning": [model("Qwen3.8-27B-MLX-4bit")]}
    out = build_suggestions(
        category, [source], aa_snapshot=aa, directory=tmp_path, mappings={}
    )
    assert out["qualified_tool_capable_unique_routes"] == 1
    suggestion = out["models"][0]["candidates"][0]
    assert suggestion["candidate"] == {
        "model_id": "opaque-uuid",
        "creator_id": "creator-uuid",
        "scale": "aa-intelligence-5",
    }
    assert suggestion["owner_verified"] is False
    assert suggestion["already_mapped"] is False
    assert suggestion["source"] == "artificial-analysis"
    assert "score" not in json.dumps(out)
    assert list(tmp_path.iterdir()) == []


def test_different_parameter_count_is_never_a_name_suggestion(tmp_path):
    source = {
        "source": "artificial-analysis",
        "metric": "intelligence",
        "scale": "aa-intelligence-5",
        "models": {"a": {"creator_id": "lab", "score": 70}},
    }
    aa = {
        "models": [
            {"id": "a", "name": "Gemma-4-27B", "slug": "gemma-4-27b"}
        ]
    }
    out = build_suggestions(
        {"reasoning": [model("Gemma-4-12B-MLX-8bit")]},
        [source],
        aa_snapshot=aa,
        directory=tmp_path,
    )
    assert out["models"][0]["candidates"] == []
    assert out["potential_matches_by_source"] == {}


def test_benchlm_private_coverage_caveat_and_existing_map(tmp_path):
    source = {
        "source": "benchlm-coding",
        "metric": "coding",
        "scale": "benchalign-v6-v5-coding-top100",
        "models": {"Ornith-1.5-9B": {"creator_id": "lab", "score": 80}},
    }
    raw = {
        "source": "benchlm-coding",
        "coverage": {"mode": "first-100", "retrieved": 100, "total": 179},
        "models": [{"id": "Ornith-1.5-9B", "creator_id": "lab", "score": 80}],
    }
    (tmp_path / "benchlm-coding.json").write_text(json.dumps(raw), encoding="utf-8")
    mappings = {
        "omlx/Ornith-1.5-9B-MLX-6bit": {
            "benchlm-coding": {
                "model_id": "Ornith-1.5-9B",
                "creator_id": "lab",
                "scale": "benchalign-v6-v5-coding-top100",
            }
        }
    }
    result = build_suggestions(
        {"code-gen": [model("Ornith-1.5-9B-MLX-6bit")]},
        [source],
        directory=tmp_path,
        mappings=mappings,
    )
    candidate = result["models"][0]["candidates"][0]
    assert candidate["already_mapped"] is True
    assert candidate["coverage"] == "coverage first-100: 100/179 ranked rows"
    assert candidate["owner_verified"] is False


def test_source_symlink_does_not_supply_untrusted_descriptors(tmp_path):
    source = {
        "source": "benchlm-coding",
        "metric": "coding",
        "scale": "benchalign-v6-coding",
        "models": {"candidate": {"creator_id": "lab", "score": 80}},
    }
    (tmp_path / "benchlm-coding.json").symlink_to(tmp_path / "missing")
    result = build_suggestions(
        {"code-gen": [model("candidate")]},
        [source],
        directory=tmp_path,
    )
    assert "symlink" in result["sources"][0]["coverage"]


def test_category_observer_gets_deep_copy_and_preserves_baseline_roles():
    fleet = [
        {"name": "coder-a:7b", "provider": "ollama"},
        {"name": "coder-b:8b", "provider": "ollama"},
    ]

    def details(name, provider="ollama"):
        return {
            "capabilities": ["completion", "thinking", "tools"],
            "param_count": 7 if name.startswith("coder-a") else 8,
            "is_moe": False,
        }

    captured = {}

    def collect(categories):
        captured.update(categories)
        categories["code-gen"].clear()

    with patch.object(tier_resolve, "get_model_details", side_effect=details):
        original = tier_resolve.resolve_roles_from_list(fleet, evidence_mode="off")
        observed = tier_resolve.resolve_roles_from_list(
            fleet, evidence_mode="off", category_observer=collect
        )
    assert observed == original
    assert captured["code-gen"] == []
    assert observed["code-gen"] != ""



def test_mapping_suggestions_require_tools_and_role_relevant_metric(tmp_path):
    aa = {
        "source": "artificial-analysis",
        "metric": "intelligence",
        "scale": "aa-intelligence-5",
        "models": {"opaque": {"creator_id": "lab", "score": 87}},
    }
    descriptors = {"models": [{"id": "opaque", "slug": "coder-7b"}]}
    no_tools = {"name": "coder-7b", "provider": "omlx", "capabilities": ["completion"]}
    has_tools = model("coder-7b")
    result = build_suggestions(
        {"code-gen": [no_tools]}, [aa], aa_snapshot=descriptors, directory=tmp_path
    )
    assert result["qualified_tool_capable_unique_routes"] == 0
    assert result["excluded_without_tool_capability"] == 1
    result = build_suggestions(
        {"code-gen": [has_tools]}, [aa], aa_snapshot=descriptors, directory=tmp_path
    )
    assert result["models"][0]["candidates"] == []
