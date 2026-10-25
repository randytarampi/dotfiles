"""Tests for Codex configuration reconciliation."""

import importlib.util
import sys
import tomllib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parents[2]
SCRIPT = REPO_ROOT / "scripts" / "configure-codex.py"
SPEC = importlib.util.spec_from_file_location("configure_codex", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
configure_codex = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(configure_codex)


@pytest.mark.parametrize("proxy_gate", ["0", "1"])
def test_litellm_provider_requires_both_gates(monkeypatch, proxy_gate):
    monkeypatch.setenv("DOTFILES_RUN_CODEX_SETUP", "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", proxy_gate)
    assert bool(configure_codex.build_litellm_provider_config()) == (proxy_gate == "1")


def test_litellm_provider_requires_setup_gate(monkeypatch):
    monkeypatch.delenv("DOTFILES_RUN_CODEX_SETUP", raising=False)
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    assert configure_codex.build_litellm_provider_config() == ""


def test_reconcile_managed_block_repeatably_and_gate_off_preserves_user_toml(
    monkeypatch,
):
    monkeypatch.setenv("DOTFILES_RUN_CODEX_SETUP", "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("LITELLM_PORT", "4567")
    original = (
        '# User config\nmodel_provider = "openai"\n[profiles.mine]\nmodel = "x"\n'
    )
    enabled = configure_codex.reconcile_litellm_provider(original, True)
    assert configure_codex.reconcile_litellm_provider(enabled, True) == enabled
    parsed = tomllib.loads(enabled)
    assert parsed["model_provider"] == "openai"
    assert (
        parsed["model_providers"]["litellm"]["base_url"] == "http://127.0.0.1:4567/v1"
    )
    disabled = configure_codex.reconcile_litellm_provider(enabled, False)
    assert tomllib.loads(disabled)["profiles"]["mine"]["model"] == "x"
    assert "model_providers" not in tomllib.loads(disabled)


@pytest.mark.parametrize(
    "content",
    [
        '[model_providers.litellm]\nname = "User provider"\n',
        "# BEGIN DOTFILES MANAGED PROVIDER litellm\n[model_providers.litellm]\n",
        "# BEGIN DOTFILES MANAGED PROVIDER litellm\n[model_providers.other]\n# END DOTFILES MANAGED PROVIDER litellm\n",
    ],
)
def test_reconcile_refuses_unmanaged_or_ambiguous_content_unchanged(content):
    before = content.encode()
    with pytest.raises(ValueError):
        configure_codex.reconcile_litellm_provider(content, True)
    assert content.encode() == before


def test_main_writes_valid_toml_twice_and_gate_off_removes_owned_block(
    tmp_path, monkeypatch
):
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    original = (
        '# User setting\nmodel_provider = "openai"\n[profiles.mine]\nmodel = "keep"\n'
    )
    config_path.write_text(original, encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DOTFILES_RUN_CODEX_SETUP", "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setattr(
        configure_codex, "resolve_ollama_cloud_model", lambda: ("m", "note")
    )
    monkeypatch.setattr(configure_codex, "resolve_local_model", lambda: "")
    monkeypatch.setattr(configure_codex, "load_env", lambda: None)
    monkeypatch.setattr(configure_codex, "logger", MagicMock())
    snapshots = []
    for _ in range(2):
        with patch.object(sys, "argv", ["configure-codex.py"]):
            configure_codex.main()
        persisted = config_path.read_text(encoding="utf-8")
        tomllib.loads(persisted)
        snapshots.append(persisted)
    assert snapshots[0] == snapshots[1]
    assert persisted
    assert persisted.count("BEGIN DOTFILES MANAGED PROVIDER litellm") == 1
    stat = config_path.stat()
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    with patch.object(sys, "argv", ["configure-codex.py"]):
        configure_codex.main()
    assert "model_providers.litellm" not in config_path.read_text(encoding="utf-8")
    assert stat.st_ino == config_path.stat().st_ino


@pytest.mark.parametrize(
    "original",
    [
        '[model_providers.litellm]\nname = "user"\n',
        '[model_providers.meridian]\nname = "user"\n',
        '[profiles.local]\nmodel = "user"\n',
    ],
)
def test_main_collision_does_not_change_bytes_or_stat(tmp_path, monkeypatch, original):
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text(original, encoding="utf-8")
    before = config_path.stat()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DOTFILES_RUN_CODEX_SETUP", "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setattr(
        configure_codex, "resolve_ollama_cloud_model", lambda: ("m", "note")
    )
    monkeypatch.setattr(configure_codex, "resolve_local_model", lambda: "")
    monkeypatch.setattr(configure_codex, "load_env", lambda: None)
    monkeypatch.setattr(configure_codex, "logger", MagicMock())
    with patch.object(sys, "argv", ["configure-codex.py"]):
        with pytest.raises(SystemExit, match="1"):
            configure_codex.main()
    assert config_path.read_text(encoding="utf-8") == original
    assert config_path.stat().st_mtime_ns == before.st_mtime_ns
    assert config_path.stat().st_ino == before.st_ino


def test_codex_registry_is_configuration_only_and_gates_match(monkeypatch):
    import litellm_config

    row = next(
        row for row in litellm_config.CLIENT_REGISTRY if row["client"] == "codex"
    )
    assert row["classification"] == "config-only"
    assert row["credential_policy"] == "defer"
    assert row["setup_gates"] == [
        "DOTFILES_RUN_CODEX_SETUP=1",
        "DOTFILES_USE_LITELLM_PROXY=1",
    ]
    assert "model_providers.litellm" in row["consumer_read_path"]
