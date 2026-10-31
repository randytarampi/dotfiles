import argparse
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_SPEC = importlib.util.spec_from_file_location(
    "configure_project",
    os.path.join(TESTS_DIR, "..", "..", "configure-project.py"),
)
assert PROJECT_SPEC is not None and PROJECT_SPEC.loader is not None
configure_project = importlib.util.module_from_spec(PROJECT_SPEC)
PROJECT_SPEC.loader.exec_module(configure_project)


def test_configure_skills_writes_native_skill_deny_rules(tmp_path):
    """Skipped skills become ordered v2 rules with action "skill" and the
    skill name as resource, replacing previous skill rules only."""
    opencode_dir = tmp_path / ".opencode"
    opencode_dir.mkdir()
    config_path = opencode_dir / "opencode.json"
    config_path.write_text(
        json.dumps(
            {
                "model": "project-model",
                "permissions": [
                    {"action": "edit", "resource": "src/**", "effect": "ask"},
                    {"action": "skill", "resource": "stale", "effect": "deny"},
                ],
            }
        )
    )
    configure_project.configure_skills(tmp_path, [], [], ["one", "two"])
    config = json.loads(config_path.read_text())
    deny_rules = [
        rule for rule in config["permissions"] if rule.get("action") == "skill"
    ]
    assert deny_rules == [
        {"action": "skill", "resource": "one", "effect": "deny"},
        {"action": "skill", "resource": "two", "effect": "deny"},
    ]
    preserved = [
        rule for rule in config["permissions"] if rule.get("action") != "skill"
    ]
    assert preserved == [{"action": "edit", "resource": "src/**", "effect": "ask"}]


TIER_SPEC = importlib.util.spec_from_file_location(
    "configure_opencode_tier",
    os.path.join(TESTS_DIR, "..", "..", "configure-opencode-tier.py"),
)
assert TIER_SPEC is not None and TIER_SPEC.loader is not None
configure_opencode_tier = importlib.util.module_from_spec(TIER_SPEC)
TIER_SPEC.loader.exec_module(configure_opencode_tier)


def make_args(
    preset=None,
    local_fallback_preset=None,
    category_models=None,
    role_models=None,
):
    args = argparse.Namespace()
    args.preset = preset
    args.local_fallback_preset = local_fallback_preset
    args.category_models = category_models
    args.role_models = role_models
    return args


class GlobalTierTests(unittest.TestCase):
    def test_reads_preset_from_global_slim(self):
        with tempfile.TemporaryDirectory() as home:
            slim_dir = os.path.join(home, ".config", "opencode")
            os.makedirs(slim_dir)
            with open(
                os.path.join(slim_dir, "oh-my-opencode-slim.json"),
                "w",
                encoding="utf-8",
            ) as handle:
                json.dump({"preset": "pro-plus"}, handle)
            with mock.patch.dict(os.environ, {"HOME": home}):
                self.assertEqual(configure_project._global_tier(), "pro-plus")

    def test_missing_slim_returns_none(self):
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.dict(os.environ, {"HOME": home}):
                self.assertIsNone(configure_project._global_tier())

    def test_unreadable_slim_returns_none(self):
        with tempfile.TemporaryDirectory() as home:
            slim_dir = os.path.join(home, ".config", "opencode")
            os.makedirs(slim_dir)
            with open(
                os.path.join(slim_dir, "oh-my-opencode-slim.json"),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write("{not json")
            with mock.patch.dict(os.environ, {"HOME": home}):
                self.assertIsNone(configure_project._global_tier())

    def test_slim_without_preset_key_returns_none(self):
        with tempfile.TemporaryDirectory() as home:
            slim_dir = os.path.join(home, ".config", "opencode")
            os.makedirs(slim_dir)
            with open(
                os.path.join(slim_dir, "oh-my-opencode-slim.json"),
                "w",
                encoding="utf-8",
            ) as handle:
                json.dump({"_tiers": {}}, handle)
            with mock.patch.dict(os.environ, {"HOME": home}):
                self.assertIsNone(configure_project._global_tier())


class ProjectOverridesGlobalTests(unittest.TestCase):
    def setUp(self):
        self._home = tempfile.TemporaryDirectory()
        slim_dir = os.path.join(self._home.name, ".config", "opencode")
        os.makedirs(slim_dir)
        with open(
            os.path.join(slim_dir, "oh-my-opencode-slim.json"), "w", encoding="utf-8"
        ) as handle:
            json.dump({"preset": "pro-plus"}, handle)
        patcher = mock.patch.dict(os.environ, {"HOME": self._home.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self._home.cleanup()

    def test_no_inputs_skips(self):
        self.assertFalse(
            configure_project.project_opencode_overrides_global(make_args())
        )

    def test_same_tier_preset_skips(self):
        self.assertFalse(
            configure_project.project_opencode_overrides_global(
                make_args(preset="pro-plus")
            )
        )

    def test_different_tier_writes(self):
        self.assertTrue(
            configure_project.project_opencode_overrides_global(
                make_args(preset="anthropic")
            )
        )

    def test_local_fallback_preset_writes_even_at_same_tier(self):
        self.assertTrue(
            configure_project.project_opencode_overrides_global(
                make_args(preset="pro-plus", local_fallback_preset="local")
            )
        )

    def test_category_models_write(self):
        self.assertTrue(
            configure_project.project_opencode_overrides_global(
                make_args(preset="pro-plus", category_models=["code-gen=x"])
            )
        )

    def test_role_models_write(self):
        self.assertTrue(
            configure_project.project_opencode_overrides_global(
                make_args(preset="pro-plus", role_models=["fixer=y"])
            )
        )

    def test_missing_global_slim_writes_conservatively(self):
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.dict(os.environ, {"HOME": home}):
                self.assertTrue(
                    configure_project.project_opencode_overrides_global(
                        make_args(preset="pro-plus")
                    )
                )


class TierEffectiveConfigTests(unittest.TestCase):
    def test_merges_global_project_root_and_opencode_dir_configs(self):
        """Later configs override nested values while preserving global models."""
        with (
            tempfile.TemporaryDirectory() as project,
            tempfile.TemporaryDirectory() as home,
        ):
            opencode_dir = Path(project) / ".opencode"
            opencode_dir.mkdir()
            (opencode_dir / "opencode.json").write_text(
                json.dumps({"providers": {"litellm": {"options": {"timeout": 30}}}})
            )
            (Path(project) / "opencode.json").write_text(
                json.dumps(
                    {
                        "model": "project-model",
                        "providers": {"litellm": {"options": {"timeout": 20}}},
                    }
                )
            )
            global_dir = Path(home) / ".config" / "opencode"
            global_dir.mkdir(parents=True)
            (global_dir / "opencode.json").write_text(
                json.dumps(
                    {
                        "model": "global-model",
                        "providers": {
                            "litellm": {
                                "models": {"test": {"id": "openai/test"}},
                                "options": {
                                    "baseURL": "http://gateway/v1",
                                    "timeout": 10,
                                },
                            }
                        },
                    }
                )
            )

            with mock.patch.dict(os.environ, {"HOME": home}):
                self.assertEqual(
                    configure_opencode_tier.load_effective_opencode_config(
                        str(opencode_dir)
                    ),
                    {
                        "model": "project-model",
                        "providers": {
                            "litellm": {
                                "models": {"test": {"id": "openai/test"}},
                                "options": {
                                    "baseURL": "http://gateway/v1",
                                    "timeout": 30,
                                },
                            }
                        },
                    },
                )

    def test_project_root_overrides_global_when_opencode_dir_has_no_config(self):
        """Use project-root overrides when the .opencode config is absent."""
        with (
            tempfile.TemporaryDirectory() as project,
            tempfile.TemporaryDirectory() as home,
        ):
            opencode_dir = Path(project) / ".opencode"
            opencode_dir.mkdir()
            root_config = Path(project) / "opencode.json"
            root_config.write_text('{"model": "project-model"}\n', encoding="utf-8")
            global_dir = Path(home) / ".config" / "opencode"
            global_dir.mkdir(parents=True)
            (global_dir / "opencode.json").write_text(
                '{"model": "global-model", "providers": {"litellm": {}}}\n',
                encoding="utf-8",
            )

            with mock.patch.dict(os.environ, {"HOME": home}):
                self.assertEqual(
                    configure_opencode_tier.load_effective_opencode_config(
                        str(opencode_dir)
                    ),
                    {"model": "project-model", "providers": {"litellm": {}}},
                )

    def test_uses_global_config_when_project_has_no_consumer_config(self):
        """Fall back to the global consumer config for projects without one."""
        with (
            tempfile.TemporaryDirectory() as project,
            tempfile.TemporaryDirectory() as home,
        ):
            opencode_dir = Path(project) / ".opencode"
            opencode_dir.mkdir()
            global_config = Path(home) / ".config" / "opencode" / "opencode.json"
            global_config.parent.mkdir(parents=True)
            global_config.write_text("{}\n", encoding="utf-8")

            with mock.patch.dict(os.environ, {"HOME": home}):
                self.assertEqual(
                    configure_opencode_tier.load_effective_opencode_config(
                        str(opencode_dir)
                    ),
                    {},
                )

    def test_unresolvable_global_tier_writes_conservatively(self):
        with tempfile.TemporaryDirectory() as home:
            slim_dir = os.path.join(home, ".config", "opencode")
            os.makedirs(slim_dir)
            with open(
                os.path.join(slim_dir, "oh-my-opencode-slim.json"),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write("{broken")
            with mock.patch.dict(os.environ, {"HOME": home}):
                self.assertTrue(
                    configure_project.project_opencode_overrides_global(
                        make_args(preset="pro-plus")
                    )
                )


if __name__ == "__main__":
    unittest.main()
