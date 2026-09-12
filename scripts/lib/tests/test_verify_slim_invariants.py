from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "verify-slim-invariants.py"
SPEC = spec_from_file_location("verify_slim_invariants", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
INVARIANTS = module_from_spec(SPEC)
SPEC.loader.exec_module(INVARIANTS)


def _fixtures(nested_synth="synth"):
    tier = "test"
    presets = {
        tier: {
            "orchestrator": {"model": "primary"},
            "council": {"model": "synth"},
        }
    }
    council_presets = {
        tier: {
            "alpha": {"model": "alpha"},
            "beta": {"model": "beta"},
            "gamma": {"model": "gamma"},
        }
    }
    tiers = {
        tier: {
            "council": {
                "presets": {
                    tier: {
                        "alpha": {"model": "alpha"},
                        "beta": {"model": "beta"},
                        "gamma": {"model": "gamma"},
                        "council": {"model": nested_synth},
                    }
                }
            }
        }
    }
    return presets, council_presets, tiers


def test_primary_chain_violations_flags_primary_and_ignores_absent_or_none():
    assert INVARIANTS._primary_chain_violations(["a", "primary"], "primary", "x")
    assert INVARIANTS._primary_chain_violations(["a", "b"], "primary", "x") == []
    assert INVARIANTS._primary_chain_violations(["a"], None, "x") == []


def test_model_dedupe_violations_reports_earlier_index():
    violations = INVARIANTS._model_dedupe_violations(["a", "b", "a"], "x")
    assert violations == ["x[2] = 'a' duplicates earlier index 0"]
    assert INVARIANTS._model_dedupe_violations(["a", "b"], "x") == []


def test_preset_violations_flags_nested_synthesizer_drift():
    presets, council_presets, tiers = _fixtures(nested_synth="other")
    violations = INVARIANTS._preset_violations(presets, council_presets, tiers)
    assert any("council.model" in violation for violation in violations)


def test_preset_violations_accepts_matching_synthesizer_and_members():
    presets, council_presets, tiers = _fixtures()
    assert INVARIANTS._preset_violations(presets, council_presets, tiers) == []


def test_preset_violations_still_flags_council_member_mismatch():
    presets, council_presets, tiers = _fixtures()
    tiers["test"]["council"]["presets"]["test"]["alpha"]["model"] = "other"
    violations = INVARIANTS._preset_violations(presets, council_presets, tiers)
    assert any("alpha.model" in violation for violation in violations)
