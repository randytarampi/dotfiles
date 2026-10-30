from datetime import datetime, timedelta, timezone

import pytest

from free_presets import (
    Candidate,
    FreePresetResolutionError,
    resolve_free_preset,
)

NOW = datetime(2026, 10, 30, tzinfo=timezone.utc)
FRESH_UNTIL = NOW + timedelta(hours=1)


def candidate(
    ref,
    host,
    model,
    *,
    capabilities=None,
    trusted=True,
    expires=FRESH_UNTIL,
    observed=NOW - timedelta(minutes=1),
    provenance="test fixture",
):
    return Candidate(
        serving_ref=ref,
        upstream_host=host,
        upstream_model=model,
        provenance=provenance,
        observed_at=observed,
        expires_at=expires,
        capabilities=(
            capabilities
            if capabilities is not None
            else {
                "text_output": True,
                "tool_call": True,
                "reasoning": True,
                "image_input": True,
            }
        ),
        source_binding_trusted=trusted,
    )


def test_direct_opencode_route_and_gateway_transport_are_classified_by_upstream():
    direct = candidate("alias-direct", "opencode", "opencode/big-pickle")
    gateway = candidate("alias-gateway", "opencode", "opencode/big-pickle")
    foreign = candidate("alias-foreign", "openrouter", "opencode/big-pickle")

    result = resolve_free_preset([foreign, gateway, direct], "free", {}, now=NOW)

    assert result.role_choices["orchestrator"].serving_ref == "alias-direct"
    assert result.audit["excluded"]["alias-foreign"]


def test_alias_with_paid_or_untrusted_backend_is_excluded_as_a_group():
    candidates = [
        candidate("shared", "cohere", "cohere/command-a"),
        candidate("shared", "openai", "openai/paid-model"),
    ]
    with pytest.raises(FreePresetResolutionError):
        resolve_free_preset(candidates, "free", {}, now=NOW)


def test_expired_unknown_capability_and_missing_vision_candidates_are_excluded():
    expired = candidate("expired", "cohere", "cohere/command", expires=NOW)
    unknown_reasoning = candidate(
        "unknown-reasoning",
        "cohere",
        "cohere/command-2",
        capabilities={"text_output": True, "tool_call": True},
    )
    text_only = candidate(
        "text-only",
        "cohere",
        "cohere/command-3",
        capabilities={"text_output": True, "tool_call": True, "reasoning": True},
    )

    with pytest.raises(FreePresetResolutionError):
        resolve_free_preset(
            [expired, unknown_reasoning, text_only], "free", {}, now=NOW
        )


def test_candidates_are_deduplicated_and_order_independent():
    items = [
        candidate("z-alias", "cohere", "cohere/command"),
        candidate("a-alias", "cohere", "cohere/command"),
        candidate("other", "poolside", "poolside/model"),
    ]
    first = resolve_free_preset(items, "free", {}, now=NOW)
    second = resolve_free_preset(list(reversed(items)), "free", {}, now=NOW)

    assert first == second
    assert first.role_choices["orchestrator"].serving_ref == "a-alias"


def test_paid_candidate_cannot_override_free_candidate():
    free = candidate("free", "cohere", "cohere/command")
    paid = candidate("paid", "openai", "openai/paid")
    result = resolve_free_preset([paid, free], "free", {}, now=NOW)
    assert all(
        choice.upstream_model == "cohere/command"
        for choice in result.role_choices.values()
    )


def test_zen_preset_rejects_foreign_host_even_when_model_name_is_free():
    foreign = candidate("foreign", "openrouter", "vendor/model:free")
    with pytest.raises(FreePresetResolutionError):
        resolve_free_preset([foreign], "omo-slim-opencode-zen-free", {}, now=NOW)


def test_zen_preset_rejects_mixed_host_alias_and_mixed_backend_capabilities():
    alias_mixed = [
        candidate("shared", "opencode", "opencode/zen:free"),
        candidate("shared", "openrouter", "vendor/model:free"),
    ]
    with pytest.raises(FreePresetResolutionError):
        resolve_free_preset(alias_mixed, "omo-slim-opencode-zen-free", {}, now=NOW)

    capability_mixed = [
        candidate("vision-alias", "opencode", "opencode/vision:free"),
        candidate(
            "vision-alias",
            "opencode",
            "opencode/vision-tool:free",
            capabilities={"text_output": True, "tool_call": True, "reasoning": True},
        ),
    ]
    with pytest.raises(FreePresetResolutionError):
        resolve_free_preset(capability_mixed, "omo-slim-opencode-zen-free", {}, now=NOW)


@pytest.mark.parametrize(
    "observed,expires",
    [
        (NOW + timedelta(seconds=1), FRESH_UNTIL),
        (NOW - timedelta(minutes=1), NOW),
    ],
)
def test_future_or_expired_timestamps_fail_closed(observed, expires):
    item = candidate(
        "bad-time", "cohere", "cohere/command", observed=observed, expires=expires
    )
    with pytest.raises(FreePresetResolutionError):
        resolve_free_preset([item], "free", {}, now=NOW)


def test_malformed_candidate_metadata_fails_closed():
    malformed = candidate("bad", "cohere", "cohere/command", provenance="")
    object.__setattr__(malformed, "observed_at", "not-a-time")
    with pytest.raises(FreePresetResolutionError, match="bad"):
        resolve_free_preset([malformed], "free", {}, now=NOW)


def test_role_preferences_council_diversity_and_capability_safe_fallbacks():
    image_a = candidate("image-a", "cohere", "cohere/command-a")
    image_b = candidate("image-b", "poolside", "poolside/model-b")
    text_only = candidate(
        "text-only",
        "cohere",
        "cohere/command-text",
        capabilities={"text_output": True, "tool_call": True, "reasoning": True},
    )
    preferences = {
        "orchestrator": [("poolside", "poolside/model-b")],
        "council-alpha": [("poolside", "poolside/model-b")],
        "council-beta": [
            ("poolside", "poolside/model-b"),
            ("cohere", "cohere/command-a"),
        ],
        "council-gamma": [
            ("poolside", "poolside/model-b"),
            ("cohere", "cohere/command-a"),
        ],
        "designer": [("cohere", "cohere/command-a")],
    }
    result = resolve_free_preset(
        [image_a, image_b, text_only], "free", preferences, now=NOW
    )

    assert result.role_choices["orchestrator"] == image_b
    assert [item.serving_ref for item in result.council] == [
        "image-b",
        "image-a",
        "text-only",
    ]
    assert [item.serving_ref for item in result.fallback["designer"]] == ["image-b"]
    assert text_only not in result.fallback["designer"]
    assert result.audit["selected"]["designer"] == "image-a"


def test_duplicate_aliases_do_not_duplicate_role_fallbacks():
    items = [
        candidate("z-primary", "cohere", "cohere/command-a"),
        candidate("a-primary", "cohere", "cohere/command-a"),
        candidate("z-fallback", "poolside", "poolside/model-b"),
        candidate("a-fallback", "poolside", "poolside/model-b"),
    ]
    first = resolve_free_preset(items, "free", {}, now=NOW)
    second = resolve_free_preset(list(reversed(items)), "free", {}, now=NOW)
    assert first == second
    assert first.role_choices["orchestrator"].serving_ref == "a-primary"
    assert [item.serving_ref for item in first.fallback["orchestrator"]] == [
        "a-fallback"
    ]


def test_naive_resolution_clock_is_refused():
    with pytest.raises(FreePresetResolutionError, match="timezone-aware"):
        resolve_free_preset([], "free", {}, now=NOW.replace(tzinfo=None))
