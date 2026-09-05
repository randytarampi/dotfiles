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
    local_fallback_placeholder=None,
    local_fallback_role=None,
):
    args = argparse.Namespace()
    args.preset = preset
    args.local_fallback_preset = local_fallback_preset
    args.local_fallback_placeholder = local_fallback_placeholder
    args.local_fallback_role = local_fallback_role
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

    def test_local_fallback_placeholder_writes(self):
        self.assertTrue(
            configure_project.project_opencode_overrides_global(
                make_args(preset="pro-plus", local_fallback_placeholder=["code-gen=x"])
            )
        )

    def test_local_fallback_role_writes(self):
        self.assertTrue(
            configure_project.project_opencode_overrides_global(
                make_args(preset="pro-plus", local_fallback_role=["fixer=y"])
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
