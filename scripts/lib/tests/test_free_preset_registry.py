import copy
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

from free_preset_registry import materialize_free_presets
from free_presets import REQUIRED_ROLES, Candidate, FreePresetResolutionError

ROOT = Path(__file__).resolve().parents[3]
REGISTRY_PATH = ROOT / "configs/opencode/oh-my-opencode-slim.json"
NOW = datetime(2026, 10, 30, tzinfo=timezone.utc)
ALL_CAPABILITIES = {
    capability: True
    for role in REQUIRED_ROLES
    for capability in {
        "text_output",
        "tool_call",
        "reasoning",
        "image_input",
    }
}


def fixture_registry():
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def candidates():
    return [
        Candidate(
            serving_ref=f"route-{index}",
            upstream_host=host,
            upstream_model=(
                "opencode/big-pickle" if index == 0 else f"free-model-{index}"
            ),
            provenance=f"fixture-{index}",
            observed_at=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(hours=1),
            capabilities=ALL_CAPABILITIES.copy(),
            source_binding_trusted=True,
        )
        for index, host in enumerate(
            ["opencode", "opencode", "poolside", "another-free-host"]
        )
    ]


def zen_candidates():
    return [item for item in candidates() if item.upstream_host == "opencode"]


def assert_only_free_refs(value, allowed):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "model":
                assert child in allowed
            else:
                assert_only_free_refs(child, allowed)
    elif isinstance(value, list):
        for child in value:
            assert_only_free_refs(child, allowed)


def test_materializes_both_real_free_presets_and_tier_routes_without_mutating_input():
    original = fixture_registry()
    baseline = copy.deepcopy(original)
    unrelated = {
        key: copy.deepcopy(original["_tiers"][key])
        for key in ("pro-plus", "cohere", "poolside")
    }
    unrelated_councils = {
        key: copy.deepcopy(value)
        for key, value in original["council"]["presets"].items()
        if key not in {"free", "omo-slim-opencode-zen-free"}
    }

    updated, audit = materialize_free_presets(
        original, candidates(), ("free", "omo-slim-opencode-zen-free"), NOW
    )

    assert original == baseline
    assert {"free", "omo-slim-opencode-zen-free"} <= set(audit)
    assert updated["_tiers"]["pro-plus"] == unrelated["pro-plus"]
    assert updated["_tiers"]["cohere"] == unrelated["cohere"]
    assert updated["_tiers"]["poolside"] == unrelated["poolside"]
    assert {
        key: value
        for key, value in updated["council"]["presets"].items()
        if key not in {"free", "omo-slim-opencode-zen-free"}
    } == unrelated_councils
    allowed = {f"route-{index}" for index in range(4)}
    for name in ("free", "omo-slim-opencode-zen-free"):
        assert_only_free_refs(updated["presets"][name], allowed)
        tier = updated["_tiers"][name]
        assert_only_free_refs(tier["council"]["presets"][name], allowed)
        assert_only_free_refs(updated["council"]["presets"][name], allowed)
        assert_only_free_refs(tier["fallback"], allowed)
        assert set(tier["fallback"]) == set(original["_tiers"][name]["fallback"])
        assert all(
            len(tier["council"]["presets"][name][seat]["model"])
            for seat in ("alpha", "beta", "gamma")
        )
        assert audit[name]["eligible_count"] == (4 if name == "free" else 2)
    assert (
        updated["council"]["presets"]["free"]["beta"]["model"]
        != "google/gemini-3.8-flash"
    )


def test_paid_preferences_are_hints_only_and_unqualified_google_is_not_fallback():
    original = fixture_registry()
    original["_tiers"]["free"]["fallback"]["oracle"] = [
        "google/models/gemini-unqualified"
    ]
    updated, _ = materialize_free_presets(original, candidates(), ("free",), NOW)
    assert updated["_tiers"]["free"]["fallback"]["oracle"]
    assert "google/models/gemini-unqualified" not in json.dumps(
        updated["presets"]["free"]
    )


def test_assigned_candidate_preference_beats_lexically_first_fallback():
    original = fixture_registry()
    original["_tiers"]["free"]["fallback"]["orchestrator"] = ["opencode/free-model-1"]
    updated, _ = materialize_free_presets(original, candidates(), ("free",), NOW)
    assert updated["presets"]["free"]["orchestrator"]["model"] == "route-0"


def test_zen_policy_and_unknown_model_paths_fail_closed():
    original = fixture_registry()
    zen_pool = zen_candidates()
    updated, _ = materialize_free_presets(
        original, zen_pool, ("omo-slim-opencode-zen-free",), NOW
    )
    allowed = {candidate.serving_ref for candidate in zen_pool}
    assert_only_free_refs(
        updated["council"]["presets"]["omo-slim-opencode-zen-free"], allowed
    )
    malformed = fixture_registry()
    malformed["presets"]["free"]["custom"] = {"model": "google/paid-model"}
    with pytest.raises(FreePresetResolutionError):
        materialize_free_presets(malformed, candidates(), ("free",), NOW)


@pytest.mark.parametrize(
    "pool",
    [
        [],
        [
            Candidate(
                serving_ref="unknown",
                upstream_host="unknown",
                upstream_model="unknown-model",
                provenance="fixture",
                observed_at=NOW - timedelta(minutes=1),
                expires_at=NOW + timedelta(hours=1),
                capabilities=ALL_CAPABILITIES.copy(),
                source_binding_trusted=True,
            )
        ],
    ],
)
def test_missing_required_choices_fail_without_partial_registry(pool):
    original = fixture_registry()
    baseline = copy.deepcopy(original)
    with pytest.raises(FreePresetResolutionError):
        materialize_free_presets(original, pool, ("free",), NOW)
    assert original == baseline
