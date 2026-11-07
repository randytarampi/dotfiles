"""Section-only user .env migration tests: strict preflight and atomic recovery."""

import importlib.util
from pathlib import Path
import stat

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "migrate-env-sections.py"
SPEC = importlib.util.spec_from_file_location("migrate_env_sections", SCRIPT)
env_migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(env_migration)

EXAMPLE = (
    "# --- Provider settings ---\n"
    "# API_KEY=''\n"
    "# --- Toggle settings ---\n"
    "# DOTFILES_RUN_MCP_SETUP='0'\n"
)
ORIGINAL = (
    "# --- Provider settings ---\n"
    "DOTFILES_RUN_MCP_SETUP='1'\n"
    "API_KEY='opaque-private-value'  # owned\n"
    "# unrelated comment remains here\n"
)


def private_env(tmp_path, value=ORIGINAL):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    path = home / ".env"
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)
    example = home / "example.env"
    example.write_text(EXAMPLE, encoding="utf-8")
    return path, example


def test_moves_only_literal_assignment_without_leaking_values():
    moved, changes = env_migration.migrate(ORIGINAL, EXAMPLE)
    content = moved.decode("utf-8")
    assert changes == [("DOTFILES_RUN_MCP_SETUP", "Toggle settings")]
    assert "API_KEY='opaque-private-value'  # owned" in content
    assert "# unrelated comment remains here" in content
    assert "# --- Toggle settings ---\nDOTFILES_RUN_MCP_SETUP='1'\n" in content
    assert (
        env_migration.read_sections(content)[0]
        == env_migration.read_sections(ORIGINAL)[0]
    )
    assert env_migration.migrate(content, EXAMPLE) == (moved, [])


@pytest.mark.parametrize(
    "unsafe",
    [
        "KEY=$(touch /tmp/unsafe)\n",
        "KEY=$USER\n",
        'KEY="$USER"\n',
        'KEY="$(id)"\n',
        "KEY='one'\nKEY='two'\n",
        "KEY='one'\nexport KEY='two'\n",
        "KEY='one'\nunset KEY\n",
        "KEY=[one,two]\n",
        "KEY='one'",
        "KEY='one'\r\nKEY='two'\n",
    ],
)
def test_unsafe_or_ambiguous_content_refused(unsafe):
    with pytest.raises(ValueError):
        env_migration.migrate(unsafe, EXAMPLE)


def test_ambiguous_adjacent_comment_refuses_relocation():
    value = (
        "# --- Provider settings ---\n# user explanation\nDOTFILES_RUN_MCP_SETUP='1'\n"
    )
    with pytest.raises(ValueError, match="ambiguous adjacent comment"):
        env_migration.migrate(value, EXAMPLE)


def test_dry_run_is_redacted_and_does_not_touch_bytes_mode_or_mtime(tmp_path, capsys):
    path, example = private_env(tmp_path)
    before = (
        path.read_bytes(),
        path.stat().st_mtime_ns,
        stat.S_IMODE(path.stat().st_mode),
    )
    assert env_migration.main(["--env", str(path), "--example", str(example)]) == 0
    out = capsys.readouterr().out
    assert "DOTFILES_RUN_MCP_SETUP" in out
    assert "opaque-private-value" not in out
    assert "Preview only" in out
    assert (
        path.read_bytes(),
        path.stat().st_mtime_ns,
        stat.S_IMODE(path.stat().st_mode),
    ) == before
    assert not path.with_name(".env.i2.bak").exists()


def test_explicit_apply_keeps_backup_and_is_idempotent(tmp_path, capsys):
    path, example = private_env(tmp_path)
    before = path.read_bytes()
    assert (
        env_migration.main(["--env", str(path), "--example", str(example), "--apply"])
        == 0
    )
    backup = path.with_name(".env.i2.bak")
    assert backup.read_bytes() == before
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    after = path.read_bytes()
    assert after != before
    assert "opaque-private-value" not in capsys.readouterr().out
    assert (
        env_migration.main(["--env", str(path), "--example", str(example), "--apply"])
        == 0
    )
    assert path.read_bytes() == after
    assert backup.read_bytes() == before


def test_failed_replace_preserves_original_and_backup(tmp_path, monkeypatch):
    path, example = private_env(tmp_path)
    before = path.read_bytes()

    def denied(*_args, **_kwargs):
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(env_migration.os, "replace", denied)
    assert (
        env_migration.main(["--env", str(path), "--example", str(example), "--apply"])
        == 1
    )
    assert path.read_bytes() == before
    assert path.with_name(".env.i2.bak").read_bytes() == before
    assert not list(path.parent.glob(".env-i2-*"))


def test_symlinks_wrong_mode_and_existing_backup_refuse_without_writes(tmp_path):
    path, example = private_env(tmp_path)
    before = path.read_bytes()
    link = path.with_name("linked.env")
    link.symlink_to(path)
    assert env_migration.main(["--env", str(link), "--example", str(example)]) == 1
    path.chmod(0o644)
    assert env_migration.main(["--env", str(path), "--example", str(example)]) == 1
    path.chmod(0o600)
    path.with_name(".env.i2.bak").write_bytes(b"previous")
    assert (
        env_migration.main(["--env", str(path), "--example", str(example), "--apply"])
        == 1
    )
    assert path.read_bytes() == before
    assert path.with_name(".env.i2.bak").read_bytes() == b"previous"
