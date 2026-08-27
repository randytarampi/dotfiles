import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import tier_registry
import tier_resolve


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
