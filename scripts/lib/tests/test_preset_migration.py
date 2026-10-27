import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

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
    def test_cli_refuses_unsupported_shell_forms_without_touching_file(self):
        cases = (
            "A=foo\\bar\n",
            "A=foo'bar\n",
            "A=~root\n",
            "A=foo*\n",
            'A="foo"\n',
            "A =foo\n",
            "A= foo\n",
            "export A=plain\n",
            "export A=plain\nA=plain\n",
        )
        with tempfile.TemporaryDirectory() as temp_home:
            for source in cases:
                with self.subTest(source=source):
                    env_path = Path(temp_home) / ".env"
                    original = (source + "DOTFILES_RUN_PI='1'\n").encode()
                    env_path.write_bytes(original)
                    result = subprocess.run(
                        [
                            sys.executable,
                            MIGRATE_PATH,
                            "--env",
                            str(env_path),
                            "--apply",
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                        env={
                            "HOME": str(Path(temp_home).resolve()),
                            "PATH": "/usr/bin:/bin",
                        },
                    )
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(env_path.read_bytes(), original)
                    self.assertFalse(env_path.with_suffix(".bak").exists())

    def test_cli_accepts_literal_semantics_and_refuses_conflicts(self):
        with tempfile.TemporaryDirectory() as temp_home:
            env_path = Path(temp_home) / ".env"
            original = (
                b"DOTFILES_OPENCODE_TIER='thirtydollars'  # note\r\nA=alpha#tag\r\n"
            )
            env_path.write_bytes(original)
            safe_env = {"HOME": str(Path(temp_home).resolve()), "PATH": "/usr/bin:/bin"}
            result = subprocess.run(
                [
                    sys.executable,
                    MIGRATE_PATH,
                    "--env",
                    str(env_path),
                    "--apply",
                    "--no-backup",
                ],
                capture_output=True,
                text=True,
                env=safe_env,
                check=False,
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn(
                b"DOTFILES_OPENCODE_TIER='omo-slim-thirty-dollars'  # note\n",
                env_path.read_bytes(),
            )
            self.assertIn(b"A=alpha#tag\r\n", env_path.read_bytes())
            conflict = b"A=plain\nA='other'\nDOTFILES_RUN_PI='1'\n"
            env_path.write_bytes(conflict)
            result = subprocess.run(
                [sys.executable, MIGRATE_PATH, "--env", str(env_path), "--apply"],
                capture_output=True,
                text=True,
                env=safe_env,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(env_path.read_bytes(), conflict)

    def test_literal_values_match_shell_semantics_in_isolated_environment(self):
        values = ["bare=value@42", "'quoted value#literal'", "", "alpha#tag"]
        with tempfile.TemporaryDirectory() as temp_home:
            isolated = {"HOME": str(Path(temp_home).resolve()), "PATH": "/usr/bin:/bin"}
            for value in values:
                env_path = Path(temp_home) / ".env"
                env_path.write_text(f"VALUE={value}\n", encoding="utf-8")
                result = subprocess.run(
                    [
                        "/bin/sh",
                        "-c",
                        'set -a; . "$1"; printf %s "$VALUE"',
                        "sh",
                        str(env_path),
                    ],
                    capture_output=True,
                    env=isolated,
                    check=False,
                )
                parsed = migrate_env_gates.parse_literal_value(value)
                self.assertIsNotNone(parsed)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout.decode(), parsed[0])

    def test_existing_backup_is_preserved_on_refusal(self):
        with tempfile.TemporaryDirectory() as temp_home:
            env_path = Path(temp_home) / ".env"
            backup_path = env_path.with_suffix(".bak")
            env_path.write_bytes(b"DOTFILES_RUN_PI='1'\n")
            backup_path.write_bytes(b"existing secret backup\n")
            backup_path.chmod(0o644)
            before = (
                backup_path.read_bytes(),
                backup_path.stat().st_mode,
                backup_path.stat().st_mtime_ns,
            )
            target_before = env_path.read_bytes()
            result = subprocess.run(
                [sys.executable, MIGRATE_PATH, "--env", str(env_path), "--apply"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(
                (
                    backup_path.read_bytes(),
                    backup_path.stat().st_mode,
                    backup_path.stat().st_mtime_ns,
                ),
                before,
            )
            self.assertEqual(env_path.read_bytes(), target_before)

    def test_symlink_backup_and_hardlinked_target_refuse_without_changes(self):
        with tempfile.TemporaryDirectory() as temp_home:
            env_path = Path(temp_home) / ".env"
            env_path.write_bytes(b"DOTFILES_RUN_PI='1'\n")
            other = Path(temp_home) / "other"
            other.write_bytes(b"keep\n")
            backup = env_path.with_suffix(".bak")
            backup.symlink_to(other)
            result = subprocess.run(
                [sys.executable, MIGRATE_PATH, "--env", str(env_path), "--apply"],
                capture_output=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(env_path.read_bytes(), b"DOTFILES_RUN_PI='1'\n")
            self.assertEqual(other.read_bytes(), b"keep\n")
            backup.unlink()
            link = Path(temp_home) / "hardlink"
            os.link(env_path, link)
            result = subprocess.run(
                [sys.executable, MIGRATE_PATH, "--env", str(env_path), "--apply"],
                capture_output=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(env_path.read_bytes(), b"DOTFILES_RUN_PI='1'\n")
            self.assertFalse(backup.exists())

    def test_private_backup_mode_and_no_backup_preserves_existing_backup(self):
        with tempfile.TemporaryDirectory() as temp_home:
            env_path = Path(temp_home) / ".env"
            original = b"DOTFILES_RUN_PI='1'\n"
            env_path.write_bytes(original)
            env_path.chmod(0o644)
            result = subprocess.run(
                [sys.executable, MIGRATE_PATH, "--env", str(env_path), "--apply"],
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0)
            backup = env_path.with_suffix(".bak")
            self.assertEqual(backup.read_bytes(), original)
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
            backup.write_bytes(b"sentinel")
            result = subprocess.run(
                [
                    sys.executable,
                    MIGRATE_PATH,
                    "--env",
                    str(env_path),
                    "--apply",
                    "--no-backup",
                ],
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(backup.read_bytes(), b"sentinel")

    def test_exclusive_backup_collision_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as temp_home:
            env_path = Path(temp_home) / ".env"
            env_path.write_text("DOTFILES_RUN_PI='1'\n", encoding="utf-8")
            backup = env_path.with_suffix(".bak")
            module = migrate_env_gates
            real_open = os.open

            def collide(path, flags, mode=0o777):
                if Path(path) == backup.resolve():
                    backup.write_bytes(b"collision")
                return real_open(path, flags, mode)

            with patch.object(module.os, "open", side_effect=collide):
                with patch.object(
                    sys, "argv", [MIGRATE_PATH, "--env", str(env_path), "--apply"]
                ):
                    self.assertEqual(module.main(), 1)
            self.assertEqual(backup.read_bytes(), b"collision")
            self.assertEqual(
                env_path.read_text(encoding="utf-8"), "DOTFILES_RUN_PI='1'\n"
            )

    def test_exported_assignment_is_refused_consistently_before_backup(self):
        with tempfile.TemporaryDirectory() as temp_home:
            env_path = Path(temp_home) / ".env"
            original = b"export DOTFILES_RUN_PI='1'\n"
            env_path.write_bytes(original)
            result = subprocess.run(
                [sys.executable, MIGRATE_PATH, "--env", str(env_path), "--apply"],
                env={**os.environ, "HOME": temp_home},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertNotIn("secret-marker", result.stdout + result.stderr)
            self.assertEqual(env_path.read_bytes(), original)
            self.assertFalse(env_path.with_suffix(".bak").exists())

    def test_substituted_value_is_refused_untouched_in_home_fixture(self):
        with tempfile.TemporaryDirectory() as temp_home:
            env_path = Path(temp_home) / ".env"
            original = b"DOTFILES_RUN_PI=$(printf secret-marker)\n"
            env_path.write_bytes(original)
            result = subprocess.run(
                [sys.executable, MIGRATE_PATH, "--env", str(env_path), "--apply"],
                env={**os.environ, "HOME": temp_home},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("Refused: DOTFILES_RUN_PI", result.stdout)
            self.assertNotIn("secret-marker", result.stdout + result.stderr)
            self.assertEqual(env_path.read_bytes(), original)
            self.assertFalse(env_path.with_suffix(".bak").exists())

    def test_dry_run_redacts_values(self):
        with tempfile.TemporaryDirectory() as temp_home:
            env_path = Path(temp_home) / ".env"
            env_path.write_text("DOTFILES_RUN_PI=secret-marker\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, MIGRATE_PATH, "--env", str(env_path), "--dry-run"],
                env={**os.environ, "HOME": temp_home},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0)
            self.assertNotIn("secret-marker", result.stdout + result.stderr)
            self.assertIn("DOTFILES_RUN_PI", result.stdout)
            self.assertEqual(
                env_path.read_text(encoding="utf-8"), "DOTFILES_RUN_PI=secret-marker\n"
            )

    def test_apply_and_dry_run_are_rejected_without_touching_file(self):
        with tempfile.TemporaryDirectory() as temp_home:
            env_path = Path(temp_home) / ".env"
            env_path.write_text("DOTFILES_RUN_PI='1'\n", encoding="utf-8")
            env_path.chmod(0o640)
            before = (
                env_path.read_bytes(),
                env_path.stat().st_mode,
                env_path.stat().st_mtime_ns,
            )
            result = subprocess.run(
                [
                    sys.executable,
                    MIGRATE_PATH,
                    "--env",
                    str(env_path),
                    "--dry-run",
                    "--apply",
                ],
                env={**os.environ, "HOME": temp_home},
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(
                (
                    env_path.read_bytes(),
                    env_path.stat().st_mode,
                    env_path.stat().st_mtime_ns,
                ),
                before,
            )
            self.assertFalse(env_path.with_suffix(".bak").exists())

    def test_nonliteral_lines_refuse_whole_file_and_literals_migrate(self):
        with tempfile.TemporaryDirectory() as temp_home:
            env_path = Path(temp_home) / ".env"
            original = (
                "DOTFILES_RUN_PI='1'\nA=$VAR\nB='ok'; command\n"
                "export C=plain\nD=$(secret-marker)\n"
            )
            env_path.write_text(original, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, MIGRATE_PATH, "--env", str(env_path), "--apply"],
                env={**os.environ, "HOME": temp_home},
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(env_path.read_text(encoding="utf-8"), original)
            self.assertNotIn("secret-marker", result.stdout + result.stderr)
            env_path.write_text("DOTFILES_RUN_PI='1'\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, MIGRATE_PATH, "--env", str(env_path), "--apply"],
                env={**os.environ, "HOME": temp_home},
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0)

    def test_conflicting_duplicate_refuses_and_symlink_refuses(self):
        with tempfile.TemporaryDirectory() as temp_home:
            env_path = Path(temp_home) / ".env"
            original = "DOTFILES_RUN_PI='1'\nDOTFILES_RUN_PI='0'\n"
            env_path.write_text(original, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, MIGRATE_PATH, "--env", str(env_path), "--apply"],
                env={**os.environ, "HOME": temp_home},
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(env_path.read_text(encoding="utf-8"), original)
            link = Path(temp_home) / "link.env"
            link.symlink_to(env_path)
            result = subprocess.run(
                [sys.executable, MIGRATE_PATH, "--env", str(link), "--apply"],
                env={**os.environ, "HOME": temp_home},
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 1)

    def test_changed_during_migration_refuses_and_keeps_original(self):
        with tempfile.TemporaryDirectory() as temp_home:
            env_path = Path(temp_home) / ".env"
            original = b"DOTFILES_RUN_PI='1'\n"
            env_path.write_bytes(original)
            spec = importlib.util.spec_from_file_location(
                "migrate_env_gates_under_write", MIGRATE_PATH
            )
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            real_migrate = module.migrate_env

            def mutating(lines, dry_run):
                env_path.touch()  # change mtime mid-migration, as a race would
                return real_migrate(lines, dry_run)

            module.migrate_env = mutating
            argv = [
                "migrate-env-gates.py",
                "--env",
                str(env_path),
                "--apply",
            ]
            with patch.object(sys, "argv", argv):
                with self.assertRaises(RuntimeError):
                    module.main()
            self.assertEqual(env_path.read_bytes(), original)
            backup = env_path.with_suffix(".bak")
            self.assertTrue(backup.is_file())
            self.assertEqual(backup.read_bytes(), original)

    def test_replace_failure_preserves_original_and_complete_backup(self):
        with tempfile.TemporaryDirectory() as temp_home:
            env_path = Path(temp_home) / ".env"
            original = b"DOTFILES_RUN_PI='1'\n"
            env_path.write_bytes(original)
            env_path.chmod(0o640)
            with patch.object(
                Path, "replace", side_effect=OSError("injected replace failure")
            ):
                with patch.object(
                    sys, "argv", [MIGRATE_PATH, "--env", str(env_path), "--apply"]
                ):
                    with self.assertRaises(OSError):
                        migrate_env_gates.main()
            self.assertEqual(env_path.read_bytes(), original)
            self.assertEqual(env_path.stat().st_mode & 0o777, 0o640)
            self.assertEqual(env_path.with_suffix(".bak").read_bytes(), original)
            self.assertEqual(
                list(Path(temp_home).glob(".env.*")), [env_path.with_suffix(".bak")]
            )

    def test_second_run_is_byte_stable(self):
        with tempfile.TemporaryDirectory() as temp_home:
            env_path = Path(temp_home) / ".env"
            env_path.write_text("DOTFILES_RUN_PI='1'\n", encoding="utf-8")
            command = [
                sys.executable,
                MIGRATE_PATH,
                "--env",
                str(env_path),
                "--apply",
                "--no-backup",
            ]
            for _ in range(2):
                result = subprocess.run(
                    command,
                    env={**os.environ, "HOME": temp_home},
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0)
            self.assertEqual(
                env_path.read_bytes(),
                b"DOTFILES_RUN_PI_SETUP='1'\nCADDY_ACCESS='localhost'\n",
            )

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
