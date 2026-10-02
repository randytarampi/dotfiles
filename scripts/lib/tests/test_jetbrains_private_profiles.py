import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "generate-jetbrains-profiles.py"
SPEC = importlib.util.spec_from_file_location("junie_profiles_private_test", SCRIPT)
PROFILES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROFILES)
CONFIGURE_SPEC = importlib.util.spec_from_file_location(
    "configure_jetbrains_ai_private_test",
    Path(__file__).resolve().parents[2] / "configure-jetbrains-ai.py",
)
CONFIGURE = importlib.util.module_from_spec(CONFIGURE_SPEC)
CONFIGURE_SPEC.loader.exec_module(CONFIGURE)


@pytest.fixture
def harness(tmp_path, monkeypatch):
    home = tmp_path / "home"
    ai_dir = home / ".ai"
    ai_dir.mkdir(parents=True)
    junie_dir = home / ".junie"
    junie_dir.symlink_to(ai_dir, target_is_directory=True)
    target = junie_dir / "models"
    clients = home / ".local/share/litellm/clients"
    clients.mkdir(parents=True, mode=0o700)
    clients.chmod(0o700)
    key_file = clients / "junie.key"
    key_file.write_text("dummy-junie-key-one", encoding="utf-8")
    key_file.chmod(0o600)
    groups_path = tmp_path / "groups.json"
    groups_path.write_text(
        json.dumps(
            {
                "providers": {
                    "openai": {
                        "baseUrl": "https://api.example/v1",
                        "apiType": "OpenAICompletion",
                        "apiKeyEnv": "OPENAI_API_KEY",
                    },
                    "litellm": {
                        "baseUrl": "http://127.0.0.1:4000/v1",
                        "apiType": "OpenAICompletion",
                        "apiKeyEnv": "LITELLM_JUNIE_KEY",
                    },
                },
                "groups": {
                    "openai-main": {"provider": "openai", "primaryModel": "gpt-main"},
                    "litellm-openai-main": {
                        "provider": "litellm",
                        "primaryModel": "main-model",
                    },
                    "openai-faster": {
                        "provider": "openai",
                        "primaryModel": "gpt-faster",
                    },
                    "litellm-openai-faster": {
                        "provider": "openai",
                        "primaryModel": "gpt-fast",
                        "fasterModel": "junie-fast",
                        "fasterProvider": "litellm",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("DOTFILES_JUNIE_USE_LITELLM", "1")
    monkeypatch.setenv("LITELLM_JUNIE_KEY", "stale-env-dummy")
    monkeypatch.setattr(PROFILES, "load_env", lambda: True)
    monkeypatch.setattr(
        PROFILES.tier_registry, "load_registry", lambda: {"presets": {}}
    )
    monkeypatch.setattr(PROFILES, "list_local_ollama_models", lambda: [])
    monkeypatch.setattr(PROFILES, "append_pool_profile_specs", lambda specs: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--groups-json",
            str(groups_path),
            "--target-dir",
            str(target),
            "--local-models",
            "[]",
        ],
    )
    return home, target, key_file, groups_path


def test_generated_litellm_profiles_are_private_and_share_cli_ide_directory(
    harness, caplog
):
    home, target, key_file, _ = harness
    PROFILES.main()
    primary = json.loads((target / "litellm-openai-main.json").read_text())
    nested = json.loads((target / "litellm-openai-faster.json").read_text())
    assert primary["apiKey"] == "dummy-junie-key-one"
    assert nested["fasterModel"]["apiKey"] == "dummy-junie-key-one"
    assert nested["fasterModel"]["baseUrl"] == (
        "http://127.0.0.1:4000/v1/chat/completions"
    )
    assert (home / ".junie/models").resolve() == (home / ".ai/models").resolve()
    assert (home / ".ai/models").stat().st_mode & 0o777 == 0o700
    assert (target / "litellm-openai-main.json").stat().st_mode & 0o777 == 0o600
    assert (target / "litellm-openai-faster.json").stat().st_mode & 0o777 == 0o600
    assert "dummy-junie-key-one" not in caplog.text

    inode = (target / "litellm-openai-main.json").stat().st_ino
    (target / "litellm-openai-main.json").chmod(0o644)
    PROFILES.main()
    assert (target / "litellm-openai-main.json").stat().st_ino == inode
    assert (target / "litellm-openai-main.json").stat().st_mode & 0o777 == 0o600
    key_file.write_text("dummy-junie-key-two", encoding="utf-8")
    PROFILES.main()
    rotated = json.loads((target / "litellm-openai-main.json").read_text())
    assert rotated["apiKey"] == "dummy-junie-key-two"


def test_missing_key_removes_only_generated_litellm_profile_and_keeps_user_json(
    harness, monkeypatch
):
    _, target, key_file, _ = harness
    target.mkdir(parents=True)
    target.chmod(0o700)
    (target / "litellm-openai-main.json").write_text("stale", encoding="utf-8")
    (target / PROFILES.PROFILE_MANIFEST).write_text(
        json.dumps(["litellm-openai-main"]), encoding="utf-8"
    )
    (target / PROFILES.PROFILE_MANIFEST).chmod(0o600)
    (target / "manual.json").write_text('{"user": true}', encoding="utf-8")
    key_file.unlink()

    PROFILES.main()

    assert not (target / "litellm-openai-main.json").exists()
    assert (target / "manual.json").read_text(encoding="utf-8") == '{"user": true}'
    assert (target / "openai-main.json").exists()


def test_unsafe_key_path_and_endpoint_fail_closed(harness, monkeypatch):
    _, target, key_file, groups_path = harness
    outside = key_file.with_name("outside.key")
    outside.write_text("dummy-outside", encoding="utf-8")
    key_file.unlink()
    key_file.symlink_to(outside)
    assert PROFILES.read_junie_key() == ""

    groups = json.loads(groups_path.read_text(encoding="utf-8"))
    groups["providers"]["litellm"]["baseUrl"] = "https://example.test/v1"
    groups_path.write_text(json.dumps(groups), encoding="utf-8")
    key_file.unlink()
    key_file.write_text("dummy-junie-key", encoding="utf-8")
    key_file.chmod(0o600)
    PROFILES.main()
    assert not any(target.glob("litellm-*.json"))


def test_dry_run_never_logs_or_writes_key(harness, monkeypatch, caplog):
    _, target, _, _ = harness
    sys.argv.append("--dry-run")
    PROFILES.main()
    assert "dummy-junie-key-one" not in caplog.text
    assert "dummy-junie-key-one" not in sys.argv
    assert not target.exists()


def test_gate_off_cleans_stale_litellm_but_preserves_unrelated_profile(
    harness, monkeypatch
):
    _, target, _, _ = harness
    target.mkdir(parents=True)
    target.chmod(0o700)
    (target / "litellm-openai-main.json").write_text("stale", encoding="utf-8")
    (target / "unrelated.json").write_text('{"user": true}', encoding="utf-8")
    monkeypatch.setenv("DOTFILES_JUNIE_USE_LITELLM", "0")

    PROFILES.main()

    assert not (target / "litellm-openai-main.json").exists()
    assert (target / "unrelated.json").read_text(encoding="utf-8") == '{"user": true}'


def test_profile_symlink_is_rejected(harness):
    _, target, _, _ = harness
    target.mkdir(parents=True)
    target.chmod(0o700)
    outside = target.parent / "outside.json"
    outside.write_text("do-not-touch", encoding="utf-8")
    (target / "litellm-openai-main.json").symlink_to(outside)
    with pytest.raises(OSError, match="symlink"):
        PROFILES.main()
    assert outside.read_text(encoding="utf-8") == "do-not-touch"


def test_models_directory_symlink_is_rejected(harness):
    _, target, _, _ = harness
    outside = target.parent / "external-models"
    outside.mkdir()
    target.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError, match="symlink"):
        PROFILES.main()
    assert not list(outside.iterdir())


def test_wrapper_propagates_profile_generation_failure_without_success_message(
    monkeypatch, caplog
):
    monkeypatch.setattr(CONFIGURE, "load_env", lambda: True)
    monkeypatch.setattr(CONFIGURE, "list_local_ollama_models", lambda: [])
    monkeypatch.setattr(
        CONFIGURE.subprocess,
        "run",
        lambda _command: type("Result", (), {"returncode": 1})(),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["configure-jetbrains-ai.py", "--dry-run", "--skip", "dirs"],
    )
    with pytest.raises(SystemExit, match="1"):
        CONFIGURE.main()
    assert "JetBrains AI configured!" not in caplog.text
