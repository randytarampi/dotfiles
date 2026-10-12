"""Tests for the shared Slim tier-coverage resolution used by the doctor CLI row."""

import json

import tier_coverage


def slim_document():
    """Build a registry with nested, fallback, direct, and gateway references."""
    return {
        "presets": {
            "pro-x": {
                "orchestrator": {"model": "anthropic/claude-sonnet-5-5"},
                "librarian": {"model": "openai/gpt-6-luna"},
                "fallback": {
                    "orchestrator": [
                        "ollama-cloud/glm-5.3",
                        "google/gemini-3.5-flash-lite",
                    ]
                },
                "observer": {"model": "litellm/ollama-cloud/glm-5.3"},
            },
            "plus": {"fixer": {"model": "meridian/claude-haiku-4-5"}},
        },
        "council": {
            "presets": {
                "free": {
                    "alpha": {"model": "opencode/big-pickle"},
                    "gamma": {"model": "openrouter/nvidia/nemotron-3.5-lightning:free"},
                }
            }
        },
        "_tiers": {
            "free": {
                "council": {
                    "presets": {
                        "free": {
                            "gamma": {
                                "model": "openrouter/nvidia/nemotron-3.5-lightning:free"
                            }
                        }
                    }
                }
            }
        },
        "multiplexer": {"enabled": True},
        "disabled_agents": ["designer"],
    }


def inventory_ids():
    """Return served gateway IDs, leaving the fixture's OpenRouter refs absent."""
    return [
        "meridian/claude-sonnet-5-5",
        "openai/gpt-6-luna",
        "ollama-cloud/glm-5.3",
        "google/models/gemini-3.5-flash-lite",
        "opencode/big-pickle",
    ]


def test_collect_walks_presets_council_tiers_and_fallbacks():
    """Collect nested model refs while excluding gateway and non-model values."""
    refs = tier_coverage.collect_registry_refs(slim_document())
    locations = {entry.location: entry.ref for entry in refs}
    assert (
        locations.get("presets.pro-x.orchestrator.model")
        == "anthropic/claude-sonnet-5-5"
    )
    assert (
        locations.get("presets.pro-x.fallback.orchestrator[1]")
        == "google/gemini-3.5-flash-lite"
    )
    assert locations.get("council.presets.free.gamma.model") == (
        "openrouter/nvidia/nemotron-3.5-lightning:free"
    )
    assert "_tiers.free.council.presets.free.gamma.model" in locations
    # Transport-prefixed and non-ref values are excluded.
    assert not any(
        location.startswith("presets.pro-x.observer") for location in locations
    )
    assert not any(
        "multiplexer" in location or "disabled_agents" in location
        for location in locations
    )


def test_unresolved_reports_only_unserved_refs():
    """Report both unserved refs while honouring aliases and direct exceptions."""
    unserved = tier_coverage.unresolved_refs(slim_document(), inventory_ids())
    resolved = {(entry.location, entry.ref) for entry in unserved}
    # The openrouter NVIDIA id is unserved with this inventory.
    assert (
        "council.presets.free.gamma.model",
        "openrouter/nvidia/nemotron-3.5-lightning:free",
    ) in resolved
    assert (
        "_tiers.free.council.presets.free.gamma.model",
        "openrouter/nvidia/nemotron-3.5-lightning:free",
    ) in resolved
    # Anthropic resolves through the meridian owner; meridian itself is a
    # direct exception; google refs match their models/ spelling.
    assert not any(entry.ref.startswith("anthropic/") for entry in unserved)
    assert not any(entry.ref.startswith("meridian/") for entry in unserved)
    assert not any("gemini-3.5-flash-lite" in entry.ref for entry in unserved)
    assert len(unserved) == 2


def test_inventory_loader_known_and_unknown_shapes(tmp_path):
    """Load gateway model IDs and treat invalid or direct-mode JSON as unknown."""
    import pathlib

    # Unreadable path is UNKNOWN.
    assert (
        tier_coverage.load_inventory_from_opencode_json(pathlib.Path("/dev/null"))
        is None
    )
    populated = tmp_path / "populated.json"
    populated.write_text(
        json.dumps(
            {"provider": {"litellm": {"models": {"a": {"id": "opencode/big-pickle"}}}}}
        )
    )
    assert tier_coverage.load_inventory_from_opencode_json(populated) == [
        "opencode/big-pickle"
    ]
    # No litellm block (direct mode) is UNKNOWN, never empty-served.
    direct_mode = tmp_path / "direct-mode.json"
    direct_mode.write_text(json.dumps({"provider": {"anthropic": {}}}))
    assert tier_coverage.load_inventory_from_opencode_json(direct_mode) is None


def test_unresolved_ignores_malformed_inventory_ids():
    """Unqualified owner strings and non-strings cannot crash model matching."""
    slim = {"presets": {"test": {"oracle": {"model": "anthropic/claude-sonnet-5-5"}}}}
    malformed = ["anthropic", "meridian", None, 3, {}, []]
    assert tier_coverage.unresolved_refs(slim, malformed) == [
        tier_coverage.RegistryRef(
            "presets.test.oracle.model", "anthropic/claude-sonnet-5-5"
        )
    ]
    assert tier_coverage.unresolved_refs(slim, malformed + inventory_ids()) == []
