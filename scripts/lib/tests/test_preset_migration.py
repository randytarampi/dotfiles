import importlib.util
import os
import shutil
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
ROOT = Path(__file__).resolve().parents[3]
MAKE = shutil.which("make") or "/usr/bin/make"
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
    def test_opencode_password_migrates_losslessly_and_conflicts_refuse(self):
        migrated, _ = migrate_env_gates.migrate_env(
            [
                "export OPENCODE_SERVER_PASSWORD='quoted-secret' # legacy\n",
                "CADDY_ACCESS='localhost'\n",
            ]
        )
        self.assertEqual(
            migrated,
            [
                "export OPENCODE_PASSWORD='quoted-secret' # legacy\n",
                "CADDY_ACCESS='localhost'\n",
            ],
        )
        same, _ = migrate_env_gates.migrate_env(
            [
                "OPENCODE_PASSWORD='same'\n",
                "OPENCODE_SERVER_PASSWORD='same'\n",
                "CADDY_ACCESS='localhost'\n",
            ]
        )
        self.assertEqual(
            same, ["OPENCODE_PASSWORD='same'\n", "CADDY_ACCESS='localhost'\n"]
        )
        with self.assertRaisesRegex(ValueError, "conflicts"):
            migrate_env_gates.migrate_env(
                ["OPENCODE_PASSWORD='new'\n", "OPENCODE_SERVER_PASSWORD='old'\n"]
            )
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            migrate_env_gates.migrate_env(["OPENCODE_SERVER_PASSWORD=''\n"])

    def test_split_gates_make_workflow_preserves_independent_values(self):
        splits = [
            (old, new)
            for old, new, inherit in migrate_env_gates.MIGRATIONS
            if old == inherit
        ]
        self.assertEqual(len(splits), 3)
        for source, target in splits:
            for left, right in (("1", "0"), ("0", "1")):
                for reverse in (False, True):
                    with (
                        self.subTest(
                            target=target, values=(left, right), reverse=reverse
                        ),
                        tempfile.TemporaryDirectory() as temp,
                    ):
                        home = Path(temp)
                        env = home / ".env"
                        example = home / "example"
                        lines = [
                            f"export {source}='{left}'\n",
                            f"export {target}='{right}'\n",
                            "DOTFILES_RUN_PI='1'\n",
                        ]
                        if reverse:
                            lines.reverse()
                        original = "".join(lines).encode()
                        env.write_bytes(original)
                        env.chmod(0o600)
                        example.write_text("NEW_KEY='fixture'\n")
                        command = [
                            MAKE,
                            "-C",
                            str(ROOT),
                            "migrate-preview",
                            f"ENV_FILE={env}",
                            f"ENV_EXAMPLE={example}",
                        ]
                        before = (
                            env.read_bytes(),
                            env.stat().st_mode,
                            env.stat().st_mtime_ns,
                        )
                        result = self._run_make(command, home)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(
                            (
                                env.read_bytes(),
                                env.stat().st_mode,
                                env.stat().st_mtime_ns,
                            ),
                            before,
                        )
                        command[command.index("migrate-preview")] = "migrate"
                        result = self._run_make(command, home)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertIn(f"export {source}='{left}'", env.read_text())
                        self.assertIn(f"export {target}='{right}'", env.read_text())
                        self.assertIn("DOTFILES_RUN_PI_SETUP", env.read_text())
                        self.assertIn("NEW_KEY", env.read_text())
                        self.assertEqual(env.with_suffix(".bak").read_bytes(), original)
                        stable = (
                            env.read_bytes(),
                            env.stat().st_mode,
                            env.stat().st_mtime_ns,
                        )
                        result = self._run_make(command, home)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(
                            (
                                env.read_bytes(),
                                env.stat().st_mode,
                                env.stat().st_mtime_ns,
                            ),
                            stable,
                        )

    def test_split_gate_explicit_values_survive_migration_and_export(self):
        lines = [
            "export DOTFILES_RUN_CADDY_SETUP='1'\n",
            "DOTFILES_RUN_DDNS_SETUP='0'\n",
        ]
        migrated, changes = migrate_env_gates.migrate_env(lines)
        self.assertIn("export DOTFILES_RUN_CADDY_SETUP='1'\n", migrated)
        self.assertIn("DOTFILES_RUN_DDNS_SETUP='0'\n", migrated)
        self.assertFalse(
            any(
                "DOTFILES_RUN_DDNS_SETUP" in line and "inherited from" in line
                for line in migrated
            )
        )
        self.assertEqual(migrate_env_gates.migrate_env(migrated), (migrated, []))

    def test_split_gate_inherits_only_when_target_absent(self):
        migrated, _ = migrate_env_gates.migrate_env(["DOTFILES_RUN_CADDY_SETUP='1'\n"])
        self.assertIn(
            "DOTFILES_RUN_DDNS_SETUP='1'  # inherited from DOTFILES_RUN_CADDY_SETUP\n",
            migrated,
        )

    def test_rename_conflict_still_refuses_both_values(self):
        lines = ["DOTFILES_RUN_PI='1'\n", "DOTFILES_RUN_PI_SETUP='0'\n"]
        self.assertIn(
            "DOTFILES_RUN_PI_SETUP", migrate_env_gates.unsafe_value_keys(lines)
        )

    def test_windows_refusal_precedes_getuid_and_file_access(self):
        with (
            patch.object(migrate_env_gates.os, "name", "nt"),
            patch.object(
                migrate_env_gates.os,
                "getuid",
                side_effect=AssertionError("getuid called"),
            ),
            patch.object(sys, "argv", [MIGRATE_PATH, "--env", "/unavailable/.env"]),
            patch("builtins.print") as output,
        ):
            self.assertEqual(migrate_env_gates.main(), 1)
        self.assertIn("POSIX", output.call_args.args[0])

    def test_make_migrate_refuses_existing_shared_backup_without_sync(self):
        with tempfile.TemporaryDirectory() as temp_home:
            home = Path(temp_home)
            env_path = home / ".env"
            example_path = home / ".env.example"
            backup_path = env_path.with_suffix(".bak")
            original = b"DOTFILES_RUN_PI='1'\n"
            backup = b"private sync backup\n"
            env_path.write_bytes(original)
            env_path.chmod(0o600)
            example_path.write_text("EXTRA_KEY='value'\n")
            backup_path.write_bytes(backup)
            backup_path.chmod(0o640)
            before = (backup_path.stat().st_mode, backup_path.stat().st_mtime_ns)
            result = self._run_make(
                [
                    MAKE,
                    "-C",
                    str(ROOT),
                    "migrate",
                    f"ENV_FILE={env_path}",
                    f"ENV_EXAMPLE={example_path}",
                ],
                home,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(env_path.read_bytes(), original)
            self.assertEqual(backup_path.read_bytes(), backup)
            self.assertEqual(
                (backup_path.stat().st_mode, backup_path.stat().st_mtime_ns), before
            )
            self.assertNotIn(b"EXTRA_KEY", env_path.read_bytes())

    def _make_env(self, home):
        return {
            "HOME": str(home),
            "PATH": f"{Path(sys.executable).resolve().parent}{os.pathsep}/usr/bin:/bin",
            "MAKEFLAGS": "",
            "MFLAGS": "",
        }

    def _run_make(self, command, home):
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            env=self._make_env(home),
        )

    def test_make_migrate_preview_is_read_only_and_apply_syncs_only_on_success(self):
        with tempfile.TemporaryDirectory() as temp_home:
            home = Path(temp_home)
            env_path = home / ".env"
            example_path = home / ".env.example"
            original = b"export DOTFILES_RUN_PI='1'\n"
            env_path.write_bytes(original)
            example_path.write_text("# Extra documented key\nEXTRA_KEY='value'\n")
            env_path.chmod(0o600)
            before_stat = env_path.stat()
            before_snapshot = (
                env_path.read_bytes(),
                before_stat.st_mode,
                before_stat.st_mtime_ns,
            )
            command = [
                MAKE,
                "-C",
                str(ROOT),
                "migrate-preview",
                f"ENV_FILE={env_path}",
                f"ENV_EXAMPLE={example_path}",
            ]
            result = self._run_make(command, home)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("DOTFILES_RUN_PI_SETUP", result.stdout)
            self.assertEqual(env_path.read_bytes(), original)
            self.assertEqual(env_path.stat().st_mtime_ns, before_stat.st_mtime_ns)
            self.assertEqual(
                (
                    env_path.read_bytes(),
                    env_path.stat().st_mode,
                    env_path.stat().st_mtime_ns,
                ),
                before_snapshot,
            )
            self.assertFalse(env_path.with_suffix(".bak").exists())

            command[command.index("migrate-preview")] = "migrate"
            result = self._run_make(command, home)
            self.assertEqual(result.returncode, 0, result.stderr)
            migrated = env_path.read_bytes()
            self.assertIn(b"export DOTFILES_RUN_PI_SETUP='1'", migrated)
            self.assertIn(b"EXTRA_KEY", migrated)
            backup = env_path.with_suffix(".bak")
            self.assertEqual(backup.read_bytes(), original)
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
            self.assertEqual(env_path.stat().st_mode & 0o777, 0o600)

            migrated_stat = env_path.stat()
            result = self._run_make(command, home)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(env_path.read_bytes(), migrated)
            self.assertEqual(env_path.stat().st_mtime_ns, migrated_stat.st_mtime_ns)
            self.assertEqual(backup.read_bytes(), original)

    def test_make_migrate_refusal_does_not_sync_example_keys(self):
        with tempfile.TemporaryDirectory() as temp_home:
            home = Path(temp_home)
            env_path = home / ".env"
            example_path = home / ".env.example"
            original = b"DOTFILES_RUN_PI=$(printf unsafe)\n"
            env_path.write_bytes(original)
            example_path.write_text("NEW_KEY='value'\n")
            result = self._run_make(
                [
                    MAKE,
                    "-C",
                    str(ROOT),
                    "migrate",
                    f"ENV_FILE={env_path}",
                    f"ENV_EXAMPLE={example_path}",
                ],
                home,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(env_path.read_bytes(), original)
            self.assertFalse(env_path.with_suffix(".bak").exists())
            self.assertNotIn(b"NEW_KEY", env_path.read_bytes())

    def test_make_migrate_refusals_preserve_fixture_files(self):
        cases = ("conflict", "symlink", "backup")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temp_home:
                home = Path(temp_home)
                env_path = home / ".env"
                example_path = home / ".env.example"
                backup_path = env_path.with_suffix(".bak")
                original = b"DOTFILES_RUN_PI='1'\n"
                env_path.write_bytes(original)
                example_path.write_text("NEW_KEY='value'\n")
                if case == "conflict":
                    env_path.write_bytes(
                        b"DOTFILES_RUN_PI='1'\nDOTFILES_RUN_PI_SETUP='0'\n"
                    )
                    env_path.chmod(0o640)
                elif case == "symlink":
                    target = home / "target.env"
                    target.write_bytes(original)
                    env_path.unlink()
                    env_path.symlink_to(target)
                    target.chmod(0o640)
                else:
                    backup_path.write_bytes(b"keep existing backup")
                    backup_path.chmod(0o640)
                    env_path.chmod(0o640)

                def snapshot(path):
                    if path.is_symlink():
                        return (
                            "symlink",
                            os.readlink(path),
                            path.lstat().st_mode,
                            path.lstat().st_mtime_ns,
                        )
                    if path.exists():
                        info = path.stat()
                        return (path.read_bytes(), info.st_mode, info.st_mtime_ns)
                    return None

                snapshots = {
                    path: snapshot(path)
                    for path in (env_path, example_path, backup_path)
                }
                result = self._run_make(
                    [
                        MAKE,
                        "-C",
                        str(ROOT),
                        "migrate",
                        f"ENV_FILE={env_path}",
                        f"ENV_EXAMPLE={example_path}",
                    ],
                    home,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(
                    {path: snapshot(path) for path in snapshots}, snapshots
                )
                self.assertNotIn(
                    b"NEW_KEY",
                    env_path.read_bytes() if not env_path.is_symlink() else b"",
                )

    def test_make_old_entrypoint_previews_then_syncs_without_migrating(self):
        with tempfile.TemporaryDirectory() as temp_home:
            home = Path(temp_home)
            old_makefile = home / "Makefile.old"
            current_makefile = (ROOT / "Makefile").read_text()
            apply_recipe = 'scripts/migrate-env-gates.py --env "$(ENV_FILE)" --apply'
            self.assertEqual(current_makefile.count(apply_recipe), 1)
            old_makefile.write_text(
                current_makefile.replace(
                    apply_recipe, apply_recipe.removesuffix(" --apply")
                )
            )
            env_path = home / ".env"
            example_path = home / ".env.example"
            original = b"export DOTFILES_RUN_PI='1'\n"
            env_path.write_bytes(original)
            example_path.write_text("NEW_KEY='value'\n")
            result = self._run_make(
                [
                    MAKE,
                    "-C",
                    str(ROOT),
                    "-f",
                    str(old_makefile),
                    "migrate",
                    f"ENV_FILE={env_path}",
                    f"ENV_EXAMPLE={example_path}",
                ],
                home,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("dry-run — no changes written", result.stdout)
            old_result = env_path.read_bytes()
            self.assertNotIn(b"DOTFILES_RUN_PI_SETUP", old_result)
            self.assertIn(b"NEW_KEY", old_result)
            self.assertNotEqual(old_result, original)

    def test_cli_refuses_unsupported_shell_forms_without_touching_file(self):
        cases = (
            "A=foo\\bar\n",
            "A=foo'bar\n",
            "A=~root\n",
            "A=foo*\n",
            'A="foo"\n',
            "A =foo\n",
            "A= foo\n",
        )
        with tempfile.TemporaryDirectory() as temp_home:
            for source in cases:
                with self.subTest(source=source):
                    env_path = Path(temp_home) / ".env"
                    original = (source + "DOTFILES_RUN_PI='1'\n").encode()
                    env_path.write_bytes(original)
                    env_path.chmod(0o640)
                    original_stat = env_path.stat()
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
                    current_stat = env_path.stat()
                    self.assertEqual(current_stat.st_mode, original_stat.st_mode)
                    self.assertEqual(
                        current_stat.st_mtime_ns, original_stat.st_mtime_ns
                    )
                    self.assertFalse(env_path.with_suffix(".bak").exists())

    def test_cli_refuses_same_size_concurrent_content_change(self):
        with tempfile.TemporaryDirectory() as temp_home:
            env_path = Path(temp_home) / ".env"
            original = b"DOTFILES_RUN_PI='1'\n"
            concurrent = b"DOTFILES_RUN_PI='2'\n"
            env_path.write_bytes(original)
            original_mtime = env_path.stat().st_mtime_ns
            module = migrate_env_gates
            real_migrate = module.migrate_env

            def mutate(lines, dry_run=False):
                env_path.write_bytes(concurrent)
                os.utime(env_path, ns=(original_mtime, original_mtime))
                return real_migrate(lines, dry_run)

            with patch.object(module, "migrate_env", side_effect=mutate):
                with patch.object(
                    sys, "argv", [MIGRATE_PATH, "--env", str(env_path), "--apply"]
                ):
                    with self.assertRaisesRegex(
                        RuntimeError, "changed during migration"
                    ):
                        module.main()
            self.assertEqual(env_path.read_bytes(), concurrent)
            self.assertEqual(env_path.with_suffix(".bak").read_bytes(), original)

    def test_temp_write_failure_cleans_up_and_keeps_original_and_backup(self):
        with tempfile.TemporaryDirectory() as temp_home:
            env_path = Path(temp_home) / ".env"
            original = b"DOTFILES_RUN_PI='1'\n"
            env_path.write_bytes(original)
            real_named_temporary_file = tempfile.NamedTemporaryFile
            created = []

            class FailingWriter:
                def __init__(self, wrapped):
                    self.wrapped = wrapped
                    self.name = wrapped.name

                def __enter__(self):
                    created.append(Path(self.name))
                    self.wrapped.__enter__()
                    return self

                def __exit__(self, *args):
                    return self.wrapped.__exit__(*args)

                def writelines(self, lines):
                    raise OSError("injected temp write failure")

            with patch.object(
                tempfile,
                "NamedTemporaryFile",
                side_effect=lambda **kwargs: FailingWriter(
                    real_named_temporary_file(**kwargs)
                ),
            ):
                with patch.object(
                    sys, "argv", [MIGRATE_PATH, "--env", str(env_path), "--apply"]
                ):
                    with self.assertRaisesRegex(OSError, "temp write failure"):
                        migrate_env_gates.main()
            self.assertEqual(env_path.read_bytes(), original)
            self.assertEqual(env_path.with_suffix(".bak").read_bytes(), original)
            self.assertEqual(len(created), 1)
            self.assertFalse(created[0].exists())

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
                b"DOTFILES_OPENCODE_TIER='omo-slim-thirty-dollars'  # note\r\n",
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
            target_stat = env_path.stat()
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
            target_after = env_path.stat()
            self.assertEqual(target_after.st_mode, target_stat.st_mode)
            self.assertEqual(target_after.st_mtime_ns, target_stat.st_mtime_ns)

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

    def test_exported_assignment_is_migrated_before_backup(self):
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
            self.assertEqual(result.returncode, 0)
            self.assertNotIn("secret-marker", result.stdout + result.stderr)
            self.assertIn(b"export DOTFILES_RUN_PI_SETUP='1'", env_path.read_bytes())
            self.assertEqual(env_path.with_suffix(".bak").read_bytes(), original)

    def test_exported_literals_are_migrated_losslessly_and_keep_export(self):
        lines = [
            "  export DOTFILES_RUN_PI='1'  # enabled\r\n",
            "  export DOTFILES_RUN_PI_SETUP='1'\r\n",
            "export DOTFILES_OPENCODE_TIER='thirtydollars' # tier\r\n",
        ]
        migrated, changes = migrate_env_gates.migrate_env(lines)
        self.assertIn(
            "  # export DOTFILES_RUN_PI='1'  # enabled  # migrated to DOTFILES_RUN_PI_SETUP\r\n",
            migrated,
        )
        self.assertIn("  export DOTFILES_RUN_PI_SETUP='1'\r\n", migrated)
        self.assertIn(
            "export DOTFILES_OPENCODE_TIER='omo-slim-thirty-dollars' # tier\r\n",
            migrated,
        )
        self.assertTrue(changes)

    def test_exported_parser_preserves_attribute_and_alias_canonical_precedence(self):
        self.assertEqual(
            migrate_env_gates.parse_env_line("export A='one' # note\r\n"),
            ("", "export ", "A", "'one' # note"),
        )
        self.assertEqual(
            migrate_env_gates.unsafe_value_keys(
                ["export DOTFILES_RUN_PI='1'\n", "DOTFILES_RUN_PI_SETUP='0'\n"]
            ),
            ["DOTFILES_RUN_PI_SETUP"],
        )

    def test_exported_shell_value_and_export_parity(self):
        with tempfile.TemporaryDirectory() as temp_home:
            env_path = Path(temp_home) / ".env"
            original = "export VALUE=plain-token # keep note\n"
            env_path.write_text(original, encoding="utf-8")
            shell = '. "$1"; printf "%s:%s" "$VALUE" "$(export -p | grep -c VALUE)"'
            before = subprocess.run(
                ["/bin/sh", "-c", shell, "sh", str(env_path)],
                capture_output=True,
                env={"HOME": temp_home, "PATH": "/usr/bin:/bin"},
                check=False,
            )
            self.assertEqual(before.returncode, 0)
            self.assertEqual(before.stdout, b"plain-token:1")
            result = subprocess.run(
                [sys.executable, MIGRATE_PATH, "--env", str(env_path), "--apply"],
                capture_output=True,
                env={"HOME": temp_home, "PATH": "/usr/bin:/bin"},
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertIn("export VALUE=plain-token # keep note", env_path.read_text())
            after = subprocess.run(
                ["/bin/sh", "-c", shell, "sh", str(env_path)],
                capture_output=True,
                env={"HOME": temp_home, "PATH": "/usr/bin:/bin"},
                check=False,
            )
            self.assertEqual(after.returncode, 0)
            self.assertEqual(after.stdout, before.stdout)

    def test_generated_lines_keep_crlf_and_missing_final_terminator(self):
        source = [
            "export DOTFILES_RUN_CADDY_SETUP='1'\r\n",
            "CADDY_ALLOW_PUBLIC='1'\r\n",
        ]
        migrated, changes = migrate_env_gates.migrate_env(source)
        self.assertTrue(changes)
        self.assertTrue(all(line.endswith("\r\n") for line in migrated))
        self.assertIn("export DOTFILES_RUN_DDNS_SETUP='1'", "".join(migrated))
        for source in (["export DOTFILES_RUN_PI='1'"], ["CADDY_ALLOW_PUBLIC='1'"]):
            migrated, _ = migrate_env_gates.migrate_env(source)
            self.assertFalse(migrated[-1].endswith("\n"))
            self.assertTrue(all(line.endswith("\n") for line in migrated[:-1]))

    def test_exported_migration_output_is_shell_sourceable(self):
        original = "export DOTFILES_RUN_PI='1'\n"
        migrated, _ = migrate_env_gates.migrate_env([original])
        with tempfile.TemporaryDirectory() as temp_home:
            env_path = Path(temp_home) / ".env"
            env_path.write_text("".join(migrated), encoding="utf-8", newline="")
            result = subprocess.run(
                [
                    "/bin/sh",
                    "-c",
                    '. "$1"; printf "%s:%s" "$DOTFILES_RUN_PI_SETUP" "$(export -p | grep -c DOTFILES_RUN_PI_SETUP)"',
                    "sh",
                    str(env_path),
                ],
                capture_output=True,
                env={"HOME": temp_home, "PATH": "/usr/bin:/bin"},
                check=False,
            )
            self.assertEqual(result.returncode, 0)
            before = env_path.with_name("before.env")
            before.write_text(original, encoding="utf-8")
            prior = subprocess.run(
                [
                    "/bin/sh",
                    "-c",
                    '. "$1"; printf "%s:%s" "$DOTFILES_RUN_PI" "$(export -p | grep -c DOTFILES_RUN_PI)"',
                    "sh",
                    str(before),
                ],
                capture_output=True,
                env={"HOME": temp_home, "PATH": "/usr/bin:/bin"},
                check=True,
            )
            self.assertEqual(result.stdout, prior.stdout)
            self.assertEqual(result.stdout.decode(), "1:1")

    def test_plain_superseded_alias_is_commented_and_export_target_preserved(self):
        for lines in (
            ["export DOTFILES_RUN_PI='1'\n", "DOTFILES_RUN_PI_SETUP='1'\n"],
            ["DOTFILES_RUN_PI_SETUP='1'\n", "export DOTFILES_RUN_PI='1'\n"],
            ["DOTFILES_RUN_PI='1'\n", "DOTFILES_RUN_PI_SETUP='1'\n"],
        ):
            migrated, _ = migrate_env_gates.migrate_env(lines)
            self.assertFalse(
                any(
                    parsed
                    and parsed[2] == "DOTFILES_RUN_PI"
                    and not parsed[1].startswith("#")
                    for parsed in map(migrate_env_gates.parse_env_line, migrated)
                )
            )
            if any(line.startswith("export ") for line in lines):
                self.assertIn("export DOTFILES_RUN_PI_SETUP='1'\n", migrated)

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
                env_path.write_bytes(b"DOTFILES_RUN_PI='2'\n")
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
            self.assertEqual(env_path.read_bytes(), b"DOTFILES_RUN_PI='2'\n")
            backup = env_path.with_suffix(".bak")
            self.assertTrue(backup.is_file())
            self.assertEqual(backup.read_bytes(), original)

    def test_mtime_only_change_during_migration_refuses(self):
        with tempfile.TemporaryDirectory() as temp_home:
            env_path = Path(temp_home) / ".env"
            original = b"DOTFILES_RUN_PI='1'\n"
            env_path.write_bytes(original)
            original_mtime = env_path.stat().st_mtime_ns
            spec = importlib.util.spec_from_file_location(
                "migrate_env_mtime", MIGRATE_PATH
            )
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            real_migrate = module.migrate_env

            def mutate_mtime(lines, dry_run):
                os.utime(
                    env_path,
                    ns=(original_mtime + 1_000_000, original_mtime + 1_000_000),
                )
                return real_migrate(lines, dry_run)

            module.migrate_env = mutate_mtime
            with patch.object(
                sys, "argv", [MIGRATE_PATH, "--env", str(env_path), "--apply"]
            ):
                with self.assertRaisesRegex(RuntimeError, "changed during migration"):
                    module.main()
            self.assertEqual(env_path.read_bytes(), original)
            self.assertEqual(env_path.with_suffix(".bak").read_bytes(), original)

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
