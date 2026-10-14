from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "verify-slim-invariants.py"
SPEC = spec_from_file_location("verify_slim_invariants", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
INVARIANTS = module_from_spec(SPEC)
SPEC.loader.exec_module(INVARIANTS)


def _fixtures(nested_synth="synth", top_variant=None, nested_variant=None):
    tier = "test"
    presets = {
        tier: {
            "orchestrator": {"model": "primary"},
            "council": {"model": "synth", **_optional_variant(top_variant)},
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
                        "council": {
                            "model": nested_synth,
                            **_optional_variant(nested_variant),
                        },
                    }
                }
            }
        }
    }
    return presets, council_presets, tiers


def _optional_variant(variant):
    return {"variant": variant} if variant is not None else {}


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


def _profile_fixture():
    roles = {
        role: {"model": f"_local:{cap}"}
        for role, cap in {
            "orchestrator": "code-gen",
            "oracle": "reasoning",
            "librarian": "lightweight",
            "explorer": "lightweight",
            "designer": "code-gen",
            "fixer": "code-gen",
            "observer": "vision",
        }.items()
    }
    presets, councils, tiers = {}, {}, {}
    for tier, name in (
        ("local-quiet", "quiet"),
        ("local", "balanced"),
        ("local-loud", "loud"),
    ):
        presets[tier] = {**roles, "council": {"model": "_local:reasoning"}}
        councils[tier] = {
            seat: {"model": "_local:reasoning"} for seat in ("alpha", "beta", "gamma")
        }
        tiers[tier] = {
            "profile": {"name": name},
            "fallback": {},
            "council": {"presets": {tier: None}},
        }
    return presets, councils, tiers


def test_profile_invariants_accept_compliant_family():
    assert INVARIANTS._profile_violations(*_profile_fixture()) == []


def test_profile_family_invariant_rejects_unknown_profile_name():
    presets, councils, tiers = _profile_fixture()
    tiers["local"]["profile"]["name"] = "travel"
    assert any(
        "expected 'balanced'" in issue
        for issue in INVARIANTS._profile_violations(presets, councils, tiers)
    )


def test_profile_placeholder_invariant_rejects_nonlocal_role_reference():
    presets, councils, tiers = _profile_fixture()
    presets["local"]["oracle"]["model"] = "openai/gpt-x"
    assert any(
        "not a _local:" in issue
        for issue in INVARIANTS._profile_violations(presets, councils, tiers)
    )


def test_lane_alias_invariant_rejects_flex_role_reference():
    presets, councils, tiers = _profile_fixture()
    presets["local"]["oracle"]["model"] = "openai/gpt-x-flex"
    assert any(
        "service-lane alias" in issue
        for issue in INVARIANTS._profile_violations(presets, councils, tiers)
    )


def test_preset_violations_flags_nested_synthesizer_variant_drift():
    presets, council_presets, tiers = _fixtures(
        top_variant="max", nested_variant="high"
    )
    violations = INVARIANTS._preset_violations(presets, council_presets, tiers)
    assert any("council.variant" in violation for violation in violations)


def test_preset_violations_accepts_matching_synthesizer_variant():
    presets, council_presets, tiers = _fixtures(top_variant="max", nested_variant="max")
    assert INVARIANTS._preset_violations(presets, council_presets, tiers) == []


def test_preset_violations_still_flags_council_member_mismatch():
    presets, council_presets, tiers = _fixtures()
    tiers["test"]["council"]["presets"]["test"]["alpha"]["model"] = "other"
    violations = INVARIANTS._preset_violations(presets, council_presets, tiers)
    assert any("alpha.model" in violation for violation in violations)


def test_litellm_model_reference_requires_proxy_mode(monkeypatch):
    monkeypatch.setattr(INVARIANTS, "_model_allowlists", lambda: {"openai": {"alpha"}})
    data = {"presets": {"x": {"orchestrator": {"model": "litellm/openai/alpha"}}}}
    assert INVARIANTS._model_allowlist_violations(data, proxy_mode=True) == []
    errors = INVARIANTS._model_allowlist_violations(data, proxy_mode=False)
    assert errors and "direct mode" in errors[0]


def test_litellm_model_reference_rejects_unknown_canonical_model(monkeypatch):
    monkeypatch.setattr(INVARIANTS, "_model_allowlists", lambda: {"openai": set()})
    data = {"presets": {"x": {"orchestrator": {"model": "litellm/openai/unknown"}}}}
    assert INVARIANTS._model_allowlist_violations(data, proxy_mode=True)
