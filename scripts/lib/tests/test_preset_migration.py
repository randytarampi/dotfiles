import importlib.util
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import preset_migration

MIGRATE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "migrate-env-gates.py"
)
spec = importlib.util.spec_from_file_location("migrate_env_gates", MIGRATE_PATH)
assert spec is not None and spec.loader is not None
migrate_env_gates = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migrate_env_gates)

project_spec = importlib.util.spec_from_file_location(
    "configure_project",
    os.path.join(os.path.dirname(__file__), "..", "..", "configure-project.py"),
)
assert project_spec is not None and project_spec.loader is not None
configure_project = importlib.util.module_from_spec(project_spec)
project_spec.loader.exec_module(configure_project)


class PresetMigrationTests(unittest.TestCase):
    def test_normalizer_covers_aliases_and_current_values(self):
        expected = {
            "openai": "omo-slim-openai",
            "thirtydollars": "omo-slim-thirty-dollars",
            "opencode-zen-free": "omo-slim-opencode-zen-free",
            "free": "free",
            "omo-slim-openai": "omo-slim-openai",
        }
        for old, new in expected.items():
            self.assertEqual(preset_migration.migrate_preset_value(old), new)

    def test_env_migration_covers_each_key_and_quote_style(self):
        for key in (
            "DOTFILES_OPENCODE_TIER",
            "DOTFILES_PI_TIER",
            "DOTFILES_LOCAL_FALLBACK_PRESET",
        ):
            for quote in ("", "'", '"'):
                for old, new in preset_migration.TIER_VALUE_MIGRATIONS.items():
                    value = f"{quote}{old}{quote}"
                    lines, _ = migrate_env_gates.migrate_env([f"{key}={value}\n"])
                    self.assertIn(f"{key}={quote}{new}{quote}\n", lines)

    def test_configure_project_uses_shared_normalizer(self):
        self.assertIs(
            configure_project.migrate_preset_value,
            preset_migration.migrate_preset_value,
        )
        self.assertEqual(
            configure_project.migrate_preset_value("thirtydollars"),
            "omo-slim-thirty-dollars",
        )

    def test_env_migration_preserves_quoted_inline_comment(self):
        lines, _ = migrate_env_gates.migrate_env(
            ["DOTFILES_OPENCODE_TIER='thirtydollars'  # keep this note\n"]
        )
        self.assertEqual(
            lines[0],
            "DOTFILES_OPENCODE_TIER='omo-slim-thirty-dollars'  # keep this note\n",
        )

    def test_env_migration_preserves_unquoted_inline_comment(self):
        lines, _ = migrate_env_gates.migrate_env(
            ["DOTFILES_LOCAL_FALLBACK_PRESET=opencode-zen-free # fallback\n"]
        )
        self.assertEqual(
            lines[0],
            "DOTFILES_LOCAL_FALLBACK_PRESET=omo-slim-opencode-zen-free # fallback\n",
        )


if __name__ == "__main__":
    unittest.main()
