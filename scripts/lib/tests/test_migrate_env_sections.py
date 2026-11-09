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
        "KEY='one'\rANOTHER='two'\n",
        "KEY='nul\x00value'\n",
        "KEY='unicode\u2028separator'\n",
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


def test_trailing_attached_comment_refuses_move():
    source = (
        "# --- Provider settings ---\n"
        "DOTFILES_RUN_MCP_SETUP='1'\n"
        "# operator note follows the old gate\n"
    )
    with pytest.raises(ValueError, match="following comment"):
        env_migration.migrate(source, EXAMPLE)


def test_destination_comment_cannot_be_reassociated_with_moved_key():
    source = (
        "# --- Provider settings ---\n"
        "DOTFILES_RUN_MCP_SETUP='1'\n"
        "# --- Toggle settings ---\n"
        "# purpose of the existing section\n"
    )
    with pytest.raises(ValueError, match="destination ends"):
        env_migration.migrate(source, EXAMPLE)


def test_crlf_migration_retains_newline_style_and_values():
    source = ORIGINAL.replace("\n", "\r\n")
    after, changes = env_migration.migrate(source, EXAMPLE)
    assert changes == [("DOTFILES_RUN_MCP_SETUP", "Toggle settings")]
    assert b"\r\n" in after
    assert after.count(b"\r\n") == after.count(b"\n")
    assert (
        env_migration.read_sections(after.decode("utf-8"))[0]
        == env_migration.read_sections(source)[0]
    )


def test_mixed_line_endings_with_distinct_keys_refuse():
    source = "API_KEY='secret'\r\nDOTFILES_RUN_MCP_SETUP='1'\n"
    with pytest.raises(ValueError, match="mixed newline"):
        env_migration.migrate(source, EXAMPLE)


@pytest.mark.parametrize("apply", [False, True])
def test_invalid_preview_and_apply_leave_private_file_unchanged(
    tmp_path, capsys, apply
):
    invalid = "API_KEY='secret'\nDOTFILES_RUN_MCP_SETUP=$REMOTE\n"
    path, example = private_env(tmp_path, invalid)
    before = (
        path.read_bytes(),
        path.stat().st_mtime_ns,
        stat.S_IMODE(path.stat().st_mode),
    )
    argv = ["--env", str(path), "--example", str(example)]
    if apply:
        argv.append("--apply")
    assert env_migration.main(argv) == 1
    assert (
        path.read_bytes(),
        path.stat().st_mtime_ns,
        stat.S_IMODE(path.stat().st_mode),
    ) == before
    captured = capsys.readouterr()
    assert "secret" not in captured.err + captured.out
    assert not path.with_name(".env.i2.bak").exists()


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


def test_changed_source_during_apply_is_never_rolled_back(tmp_path, monkeypatch):
    """After backup, refuse another writer's updated value, even on failure."""
    path, example = private_env(tmp_path)
    original = path.read_bytes()
    newer = b"API_KEY='independent-owner-update'\n"
    real_guard = env_migration.guarded_file
    calls = 0

    def writer_arrives(candidate):
        nonlocal calls
        calls += 1
        if calls == 2:
            path.write_bytes(newer)
        return real_guard(candidate)

    monkeypatch.setattr(env_migration, "guarded_file", writer_arrives)
    assert (
        env_migration.main(["--env", str(path), "--example", str(example), "--apply"])
        == 1
    )
    assert path.read_bytes() == newer
    assert path.with_name(".env.i2.bak").read_bytes() == original
    assert not list(path.parent.glob(".env-i2-*"))


def test_final_window_writer_is_overwritten_without_refusal(tmp_path, monkeypatch):
    """Document the deliberate final-window limitation: no cross-process CAS.

    The guarded preflight check refuses an owner update that lands before
    replacement starts, but the identity/byte comparison immediately before
    os.replace is best-effort drift detection, not compare-and-swap. A writer
    landing inside that final window is overwritten without refusal and never
    restored automatically; the retained private backup is the only recovery
    path. This is why the documented contract requires a quiescent,
    owner-controlled maintenance window for --apply.
    """
    path, example = private_env(tmp_path)
    before = path.read_bytes()
    foreign = b"API_KEY='window-writer-arrives-late'\n"
    real_replace = env_migration.os.replace

    def writer_lands_in_final_window(src, dst):
        if Path(dst) == path:
            path.write_bytes(foreign)
        return real_replace(src, dst)

    monkeypatch.setattr(env_migration.os, "replace", writer_lands_in_final_window)
    assert (
        env_migration.main(["--env", str(path), "--example", str(example), "--apply"])
        == 0
    )
    final = path.read_bytes()
    assert final != foreign
    assert final != before
    assert path.with_name(".env.i2.bak").read_bytes() == before
    assert foreign not in final


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


TWO_LEVEL_EXAMPLE = (
    "# --- Dotfiles toggles (0 = skip, 1 = run) ---\n"
    "# == Deployment phases and service gates ==\n"
    "# DOTFILES_RUN_PACKAGES_SETUP='0'\n"
    "# == Per-client gateway integration gates ==\n"
    "# DOTFILES_RUN_CODEX_SETUP='0'\n"
    "# --- Tier settings ---\n"
    "# DOTFILES_OPENCODE_TIER=''\n"
)


def test_owner_subsection_titles_match_documented_nested_sections():
    original = (
        "# --- Deployment phases and service gates ---\n"
        "DOTFILES_RUN_PACKAGES_SETUP='1'\n"
        "# --- Per-client gateway integration gates ---\n"
        "DOTFILES_RUN_CODEX_SETUP='1'\n"
        "# --- Tier settings ---\n"
        "DOTFILES_OPENCODE_TIER='pro-plus'\n"
    )
    moved, changes = env_migration.migrate(original, TWO_LEVEL_EXAMPLE)
    assert changes == []
    assert moved == original.encode()


def test_keys_in_parent_block_relocate_to_documented_subsection_titles():
    original = (
        "# --- Dotfiles toggles (0 = skip, 1 = run) ---\n"
        "DOTFILES_RUN_PACKAGES_SETUP='1'\n"
        "DOTFILES_RUN_CODEX_SETUP='1'\n"
    )
    moved, changes = env_migration.migrate(original, TWO_LEVEL_EXAMPLE)
    assert changes == [
        ("DOTFILES_RUN_PACKAGES_SETUP", "Deployment phases and service gates"),
        ("DOTFILES_RUN_CODEX_SETUP", "Per-client gateway integration gates"),
    ]
    _, positions = env_migration.read_sections(moved.decode())
    sections = {key: section for _, key, section in positions}
    assert (
        sections["DOTFILES_RUN_PACKAGES_SETUP"] == "Deployment phases and service gates"
    )
    assert (
        sections["DOTFILES_RUN_CODEX_SETUP"] == "Per-client gateway integration gates"
    )


def test_destination_append_after_section_heading_is_unambiguous():
    original = (
        "# --- Dotfiles toggles (0 = skip, 1 = run) ---\nDOTFILES_RUN_CODEX_SETUP='1'\n"
    )
    moved, changes = env_migration.migrate(original, TWO_LEVEL_EXAMPLE)
    assert changes == [
        ("DOTFILES_RUN_CODEX_SETUP", "Per-client gateway integration gates")
    ]
    text = moved.decode()
    assert "== Per-client gateway integration gates ==" in text
    assert "DOTFILES_RUN_CODEX_SETUP='1'" in text


def test_existing_subsection_heading_block_accepts_appended_keys():
    original = (
        "DOTFILES_RUN_PACKAGES_SETUP='1'\n"
        "# == Deployment phases and service gates ==\n"
        "# --- Tier settings ---\n"
        "DOTFILES_OPENCODE_TIER='pro-plus'\n"
        "# deployment notes remain in place\n"
    )
    moved, changes = env_migration.migrate(original, TWO_LEVEL_EXAMPLE)
    assert changes == [
        ("DOTFILES_RUN_PACKAGES_SETUP", "Deployment phases and service gates")
    ]
    text = moved.decode()
    assert "# deployment notes remain in place" in text
    assert "DOTFILES_RUN_PACKAGES_SETUP='1'" in text
    _, positions = env_migration.read_sections(moved.decode())
    sections = {key: section for _, key, section in positions}
    assert (
        sections["DOTFILES_RUN_PACKAGES_SETUP"] == "Deployment phases and service gates"
    )
    assert sections["DOTFILES_OPENCODE_TIER"] == "Tier settings"


def test_created_subsection_stays_under_template_parent():
    original = (
        "# --- Dotfiles toggles (0 = skip, 1 = run) ---\n"
        "DOTFILES_RUN_PACKAGES_SETUP='1'\n"
        "# --- Tier settings ---\n"
        "DOTFILES_OPENCODE_TIER='pro-plus'\n"
        "DOTFILES_RUN_CODEX_SETUP='1'\n"
    )
    moved, changes = env_migration.migrate(original, TWO_LEVEL_EXAMPLE)
    assert changes == [
        ("DOTFILES_RUN_CODEX_SETUP", "Per-client gateway integration gates")
    ]
    text = moved.decode()
    parent = text.index("# --- Dotfiles toggles (0 = skip, 1 = run) ---")
    child = text.index("# == Per-client gateway integration gates ==")
    next_parent = text.index("# --- Tier settings ---")
    assert parent < child < next_parent
    assert env_migration.migrate(text, TWO_LEVEL_EXAMPLE)[1] == []


def test_missing_parent_is_created_before_missing_subsection():
    original = (
        "# --- Tier settings ---\n"
        "DOTFILES_OPENCODE_TIER='pro-plus'\n"
        "DOTFILES_RUN_CODEX_SETUP='1'\n"
    )
    moved, changes = env_migration.migrate(original, TWO_LEVEL_EXAMPLE)
    assert changes == [
        ("DOTFILES_RUN_CODEX_SETUP", "Per-client gateway integration gates")
    ]
    text = moved.decode()
    assert text.index("# --- Dotfiles toggles") < text.index(
        "# == Per-client gateway integration gates =="
    )
    assert env_migration.migrate(text, TWO_LEVEL_EXAMPLE)[1] == []


def test_real_template_global_deployment_gates_not_project_scoped():
    example = (
        Path(__file__).resolve().parents[3] / "dot_dotfiles/shell/.env.example"
    ).read_text(encoding="utf-8")
    targets = env_migration.template_sections(example)
    for key in (
        "DOTFILES_RUN_OLLAMA_DAEMON_SETUP",
        "DOTFILES_RUN_OPENCODE_SETUP",
        "DOTFILES_RUN_PI_SETUP",
        "DOTFILES_RUN_PASEO_SETUP",
        "DOTFILES_RUN_OPENCODE_WEB_SETUP",
    ):
        assert targets[key] == "Deployment phases and service gates"
    assert targets["DOTFILES_PROJECT_STEPS"].startswith("Project-scoped configuration")
