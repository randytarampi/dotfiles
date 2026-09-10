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
