import importlib.util
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import tier_registry
import tier_resolve

VERIFY_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "verify-slim-invariants.py"
)
verify_spec = importlib.util.spec_from_file_location(
    "verify_slim_invariants", VERIFY_PATH
)
assert verify_spec is not None
assert verify_spec.loader is not None
verify_slim_invariants = importlib.util.module_from_spec(verify_spec)
verify_spec.loader.exec_module(verify_slim_invariants)


def load_script_module(name, filename):
    path = os.path.join(os.path.dirname(__file__), "..", "..", filename)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


configure_pi = load_script_module("configure_pi", "configure-pi.py")
configure_voice = load_script_module(
    "configure_opencode_voice", "configure-opencode-voice.py"
)


def registry():
    return tier_registry.load_registry(
        os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "..",
            "configs",
            "opencode",
            "oh-my-opencode-slim.json",
        )
    )


class TierRegistryTests(unittest.TestCase):
    def test_pi_excludes_opencode_only_presets(self):
        tiers = set(configure_pi.get_pi_available_tiers())
        self.assertNotIn("openai", tiers)
        self.assertNotIn("thirtydollars", tiers)
        self.assertNotIn("opencode-zen-free", tiers)

    def test_voice_mapping_for_opencode_only_presets_uses_openai_defaults(self):
        with patch.object(
            configure_voice, "check_ollama_daemon", return_value=(None, False)
        ), patch.object(
            configure_voice, "list_local_ollama_models", return_value=[]
        ), patch.dict(
            "os.environ", {"DOTFILES_USE_LOCAL_OLLAMA": "0"}
        ):
            for tier in ("openai", "thirtydollars", "opencode-zen-free"):
                config = configure_voice.get_voice_config(tier)
                self.assertEqual(config["model"], "gpt-5.4-mini")

    def test_materialize_role_models_tolerates_absent_observer(self):
        data = registry()
        self.assertNotIn("observer", data["presets"]["pro-plus"])
        roles = tier_registry.materialize_role_models(data, "pro-plus", {})
        self.assertNotIn("observer", roles)
        self.assertIn("orchestrator", roles)

    def test_zen_free_uses_current_multimodal_orchestrator_without_observer(self):
        data = registry()
        preset = data["presets"]["opencode-zen-free"]
        self.assertEqual(
            preset["orchestrator"]["model"],
            "opencode/muse-spark-1.2-contributor-free",
        )
        self.assertNotIn("observer", preset)
        self.assertNotIn("observer", data["_tiers"]["opencode-zen-free"]["fallback"])

    def test_provider_dedupe_flags_duplicate_provider(self):
        violations = verify_slim_invariants._provider_dedupe_violations(
            ["openai/first", "openai/second"], "fallback.role"
        )
        self.assertEqual(len(violations), 1)

    def test_provider_dedupe_accepts_distinct_providers(self):
        self.assertEqual(
            verify_slim_invariants._provider_dedupe_violations(
                ["openai/model", "anthropic/model"]
            ),
            [],
        )

    def test_provider_dedupe_allows_primary_provider_overlap(self):
        self.assertEqual(
            verify_slim_invariants._provider_dedupe_violations(["openai/fallback"]),
            [],
        )

    def test_role_override_does_not_change_category(self):
        roles = tier_registry.materialize_role_models(
            registry(),
            "local-pro",
            {"code-gen": "ollama/code", "reasoning": "ollama/reason"},
            ["fixer=ollama/fixer"],
        )
        self.assertEqual(roles["fixer"], "ollama/fixer")
        self.assertEqual(roles["orchestrator"], "ollama/code")

    def test_placeholder_override_changes_all_roles(self):
        categories = {"code-gen": "ollama/code", "reasoning": "ollama/reason"}
        tier_registry.apply_placeholder_overrides(categories, ["code-gen=ollama/new"])
        roles = tier_registry.materialize_role_models(
            registry(), "local-pro", categories
        )
        self.assertEqual(roles["orchestrator"], "ollama/new")
        self.assertEqual(roles["fixer"], "ollama/new")

    def test_local_solo_resolves_every_role(self):
        roles = tier_registry.materialize_role_models(
            registry(), "local-solo", {"solo": "ollama/solo"}
        )
        self.assertTrue(roles)
        self.assertEqual(set(roles.values()), {"ollama/solo"})

    def test_moe_codegen_reuse_requires_metadata_and_vision(self):
        models = ["qwen3-coder:30b", "qwen3-vl:8b", "phi:3b"]

        def model_details(model_name):
            if model_name == "qwen3-coder:30b":
                return {
                    "param_count": 30,
                    "capabilities": ["completion", "thinking", "tools", "vision"],
                    "is_moe": True,
                }
            if model_name == "qwen3-vl:8b":
                return {
                    "param_count": 8,
                    "capabilities": ["completion", "tools", "vision"],
                    "is_moe": False,
                }
            return {
                "param_count": 3,
                "capabilities": ["completion", "tools"],
                "is_moe": False,
            }

        with patch.object(tier_resolve, "get_model_details", side_effect=model_details):
            reused = tier_resolve.resolve_roles_from_list(
                models, moe_codegen_reuse=True
            )
            not_reused = tier_resolve.resolve_roles_from_list(
                models, moe_codegen_reuse=False
            )

        self.assertEqual(reused["lightweight"], reused["code-gen"])
        self.assertEqual(reused["vision"], reused["code-gen"])
        self.assertNotEqual(not_reused["lightweight"], not_reused["code-gen"])
        self.assertNotEqual(not_reused["vision"], not_reused["code-gen"])

        def no_vision_details(model_name):
            details = model_details(model_name)
            if model_name == "qwen3-coder:30b":
                details["capabilities"] = ["completion", "thinking", "tools"]
            return details

        with patch.object(
            tier_resolve, "get_model_details", side_effect=no_vision_details
        ):
            no_vision = tier_resolve.resolve_roles_from_list(
                models, moe_codegen_reuse=True
            )

        self.assertEqual(no_vision["lightweight"], no_vision["code-gen"])
        self.assertNotEqual(no_vision["vision"], no_vision["code-gen"])

    def test_cloud_has_no_placeholders(self):
        self.assertFalse(tier_registry.uses_local_placeholders(registry(), "pro"))

    def test_malformed_overrides_raise(self):
        with self.assertRaises(ValueError):
            tier_registry.apply_placeholder_overrides({}, ["bad"])
        with self.assertRaises(ValueError):
            tier_registry.materialize_role_models(registry(), "pro", {}, ["bad"])

    def test_positional_style_conflict_is_not_registry_concern(self):
        self.assertEqual(
            tier_registry.collect_placeholder_categories("_local:audio"), "audio"
        )


if __name__ == "__main__":
    unittest.main()
