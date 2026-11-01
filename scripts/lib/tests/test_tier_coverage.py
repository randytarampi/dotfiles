"""Tests for the shared Slim tier-coverage resolution used by the doctor CLI row."""

import json
import ast
import importlib.util
from pathlib import Path

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
    # Routed transport aliases must also be checked (not silently skipped).
    assert locations["presets.pro-x.observer.model"] == "litellm/ollama-cloud/glm-5.3"
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


def test_local_direct_exceptions_and_slim_gate_parity():
    """Local direct refs bypass inventory, but cloud refs remain gateway-only."""
    slim = {
        "presets": {
            "test": {
                "a": {"model": "omlx/whatever-id"},
                "b": {"model": "ollama/some-tag"},
                "c": {"model": "openai/foo"},
            }
        }
    }
    unserved = tier_coverage.unresolved_refs(slim, [])
    assert [(entry.ref) for entry in unserved] == ["openai/foo"]

    script = Path(__file__).resolve().parents[2] / "configure-opencode.py"
    spec = importlib.util.spec_from_file_location("configure_opencode_for_test", script)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    gate = next(
        node
        for node in ast.parse(script.read_text(encoding="utf-8")).body
        if isinstance(node, ast.FunctionDef) and node.name == "apply_litellm_slim_gate"
    )
    policies = {}
    for node in ast.walk(gate):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {
                    "direct_exceptions",
                    "local_owners",
                }:
                    policies[target.id] = ast.literal_eval(node.value)
    assert set(policies) == {"direct_exceptions", "local_owners"}
    assert tier_coverage.DIRECT_EXCEPTIONS == (
        policies["direct_exceptions"] | policies["local_owners"]
    )


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
    empty_models = tmp_path / "empty-models.json"
    empty_models.write_text(json.dumps({"provider": {"litellm": {"models": {}}}}))
    assert tier_coverage.load_inventory_from_opencode_json(empty_models) == []
    # No litellm block (direct mode) is UNKNOWN, never empty-served.
    direct_mode = tmp_path / "direct-mode.json"
    direct_mode.write_text(json.dumps({"provider": {"anthropic": {}}}))
    assert tier_coverage.load_inventory_from_opencode_json(direct_mode) is None


def test_inventory_loader_native_providers_shape(tmp_path):
    """The doctor row and deploy gate read the native v2 providers key."""

    populated = tmp_path / "native-populated.json"
    populated.write_text(
        json.dumps(
            {"providers": {"litellm": {"models": {"a": {"id": "opencode/big-pickle"}}}}}
        )
    )
    assert tier_coverage.load_inventory_from_opencode_json(populated) == [
        "opencode/big-pickle"
    ]
    direct_mode = tmp_path / "native-direct-mode.json"
    direct_mode.write_text(json.dumps({"providers": {"anthropic": {}}}))
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


def test_routed_aliases_use_exact_inventory_ids_including_fallbacks():
    slim = {
        "presets": {
            "free": {
                "orchestrator": {"model": "litellm/zen"},
                "fallback": {"orchestrator": ["litellm/other/vendor:free"]},
            }
        }
    }
    assert tier_coverage.unresolved_refs(slim, ["zen", "other/vendor:free"]) == []
    missing = tier_coverage.unresolved_refs(slim, ["zen"])
    assert len(missing) == 1
    assert missing[0].ref == "litellm/other/vendor:free"
    assert len(tier_coverage.unresolved_refs(slim, ["other/vendor:free"])) == 1
