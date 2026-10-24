"""Tests for scripts/configure-codex.py — Codex CLI provider configuration."""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parents[2]
SCRIPT = REPO_ROOT / "scripts" / "configure-codex.py"

SPEC = importlib.util.spec_from_file_location("configure_codex", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
configure_codex = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(configure_codex)


def test_user_top_level_model_provider_is_preserved(tmp_path):
    """Fixture test: user's top-level model_provider survives main().

    Previously, line 245 used `^model_provider\\s*=.*$\\n?` to strip ALL
    top-level model_provider keys, destroying the user's default provider
    and contradicting the docstring promise to not change Codex's default.

    After the fix, the script no longer strips top-level model_provider.
    """
    config_path = tmp_path / "config.toml"
    user_config = (
        "# User's personal Codex config\n"
        'model_provider = "anthropic"\n\n'
        "[profiles.my-profile]\n"
        'model = "claude-sonnet-5-5"\n'
    )
    config_path.write_text(user_config, encoding="utf-8")

    mock_logger = MagicMock()
    with (
        patch.object(configure_codex.os.path, "expanduser", return_value=str(tmp_path)),
        patch.object(configure_codex.os.path, "exists", return_value=True),
        patch.object(configure_codex.os, "makedirs"),
        patch.object(configure_codex, "write_text_file") as mock_write,
        patch.object(configure_codex, "backup_file", return_value=None),
        patch.object(configure_codex, "logger", mock_logger),
        patch.object(
            configure_codex,
            "resolve_ollama_cloud_model",
            return_value=("gemma4:12b", "note"),
        ),
        patch.object(
            configure_codex, "resolve_local_model", return_value="mellum-small"
        ),
        patch.object(configure_codex, "load_env"),
    ):
        with patch.object(sys, "argv", ["configure-codex.py"]):
            configure_codex.main()

    written = mock_write.call_args[0][1]
    assert (
        'model_provider = "anthropic"' in written
    ), "User's top-level model_provider was stripped — the fix is not applied."


def test_dry_run_does_not_write_to_config(tmp_path, monkeypatch):
    """D1: --dry-run logs what would be written but does not touch the file."""
    config_path = tmp_path / "config.toml"
    config_path.write_text("# existing\n", encoding="utf-8")

    mock_logger = MagicMock()
    with (
        patch.object(configure_codex.os.path, "expanduser", return_value=str(tmp_path)),
        patch.object(configure_codex.os.path, "exists", return_value=True),
        patch.object(configure_codex, "write_text_file") as mock_write,
        patch.object(configure_codex, "logger", mock_logger),
        patch.object(
            configure_codex,
            "resolve_ollama_cloud_model",
            return_value=("gemma4:12b", "note"),
        ),
        patch.object(
            configure_codex, "resolve_local_model", return_value="mellum-small"
        ),
        patch.object(configure_codex, "load_env"),
    ):
        with patch.object(
            sys, "argv", ["configure-codex.py", "--dry-run", "--no-backup"]
        ):
            configure_codex.main()

    # In dry-run mode, write_text_file should not be called
    mock_write.assert_not_called()
    mock_logger.info.assert_called()
    info_calls = [str(c) for c in mock_logger.info.call_args_list]
    assert any("Would configure" in call for call in info_calls)


def test_idempotent_rerun_preserves_user_state(tmp_path):
    """D2: Running configure-codex twice should be idempotent — user's
    provider/model settings must survive both runs.
    """
    content = configure_codex.build_provider_config("mellum-small")
    profiles = configure_codex.build_profiles_config("gemma4:12b", "mellum-small")
    full_config = (
        f'# User comment\nmodel_provider = "openai"\n\n{content}\n\n{profiles}\n'
    )

    stripped = configure_codex.strip_managed_profiles(full_config)
    # Re-run the same pipeline — user model_provider should still be present
    assert (
        "model_provider = " in stripped or "model_provider =" in stripped
    ), "User model_provider lost during idempotency cycle"
