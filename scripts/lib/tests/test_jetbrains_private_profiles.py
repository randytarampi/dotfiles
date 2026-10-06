import importlib.util
import json
from pathlib import Path
import sys

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
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
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


def test_litellm_faster_model_stays_on_proxy_and_direct_group_stays_direct(
    harness, monkeypatch
):
    _, target, _, groups_path = harness
    config = json.loads(groups_path.read_text())
    config["providers"]["google"] = {
        "baseUrl": "https://generativelanguage.googleapis.com/v1beta/openai",
        "apiType": "OpenAICompletion",
        "apiKeyEnv": "GEMINI_API_KEY",
    }
    config["groups"].update(
        {
            "google-flash": {
                "provider": "google",
                "primaryModel": "gemini-3.8-flash",
                "fasterModel": "gemini-3.5-flash-lite",
                "fasterProvider": "google",
            },
            "litellm-google-flash": {
                "provider": "litellm",
                "primaryModel": "google/models/gemini-3.8-flash",
                "fasterModel": "google/models/gemini-3.5-flash-lite",
            },
        }
    )
    groups_path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-google-key")
    monkeypatch.setattr(
        PROFILES,
        "litellm_catalogue_models",
        lambda *_: {
            "main-model",
            "google/models/gemini-3.8-flash",
            "google/models/gemini-3.5-flash-lite",
        },
    )

    PROFILES.main()

    proxy = json.loads((target / "litellm-google-flash.json").read_text())
    assert proxy["fasterModel"]["id"] == "google/models/gemini-3.5-flash-lite"
    assert "baseUrl" not in proxy["fasterModel"]
    assert "apiKey" not in proxy["fasterModel"]
    assert proxy["baseUrl"] == "http://127.0.0.1:4000/v1/chat/completions"
    assert proxy["apiKey"] == "dummy-junie-key-one"
    assert (target / "litellm-google-flash.json").stat().st_mode & 0o777 == 0o600

    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    PROFILES.main()
    direct = json.loads((target / "google-flash.json").read_text())
    assert direct["fasterModel"]["id"] == "gemini-3.5-flash-lite"
    assert "baseUrl" not in direct["fasterModel"]
    assert direct["baseUrl"].startswith("https://generativelanguage.googleapis.com/")


def test_litellm_group_rejects_explicit_direct_faster_provider(harness, caplog):
    _, target, _, groups_path = harness
    config = json.loads(groups_path.read_text())
    config["groups"]["litellm-openai-faster"]["provider"] = "litellm"
    config["groups"]["litellm-openai-faster"]["fasterProvider"] = "google"
    groups_path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(SystemExit):
        PROFILES.main()
    assert "cannot use direct faster provider 'google'" in caplog.text
    assert not (target / "litellm-openai-faster.json").exists()


def test_tier_and_pool_profiles_proxy_or_omit_exact_aliases(harness, monkeypatch):
    _, target, _, groups_path = harness
    target.mkdir(parents=True)
    (target / "plus.json").write_text('{"id":"stale-direct"}', encoding="utf-8")
    (target / PROFILES.PROFILE_MANIFEST).write_text(
        json.dumps(["plus"]), encoding="utf-8"
    )
    (target / PROFILES.PROFILE_MANIFEST).chmod(0o600)
    config = json.loads(groups_path.read_text())
    config["groups"] = {
        "openai-custom": {"provider": "openai", "primaryModel": "custom/alias"},
        "litellm-openai-custom": {
            "provider": "litellm",
            "primaryModel": "openai/custom/alias",
        },
    }
    groups_path.write_text(json.dumps(config), encoding="utf-8")
    roles = {
        "pro": ("ollama-cloud/llama:cloud", "ollama-cloud/qwen:cloud"),
        "local-pro": ("omlx/local/model", "omlx/local/faster"),
        "plus": ("openai/gpt-missing", "openai/gpt-faster"),
        "free": ("opencode/free-present", "openai/gpt-faster-absent"),
        "meridian": ("anthropic/claude-sonnet", "openai/gpt-present"),
    }
    registry = {"presets": {name: {} for name in roles}}
    monkeypatch.setattr(PROFILES.tier_registry, "load_registry", lambda: registry)
    monkeypatch.setattr(
        PROFILES.tier_registry, "uses_local_placeholders", lambda *_: False
    )
    monkeypatch.setattr(
        PROFILES.tier_registry,
        "materialize_role_models",
        lambda _registry, tier, *_args: {
            "orchestrator": roles[tier][0],
            "librarian": roles[tier][1],
        },
    )
    monkeypatch.setattr(PROFILES, "resolve_engine", lambda *_: None)
    monkeypatch.setattr(
        PROFILES,
        "build_provider_configs",
        lambda *_: {
            provider: {
                "baseUrl": f"https://direct.example/{provider}/v1",
                "apiType": "OpenAICompletion",
                "apiKey": f"${{{provider.upper()}_KEY}}",
            }
            for provider in (
                "litellm",
                "ollama-cloud",
                "omlx",
                "openai",
                "opencode",
                "meridian",
            )
        }
        | {
            "litellm": {
                "baseUrl": "http://127.0.0.1:4000/v1",
                "apiType": "OpenAICompletion",
                "apiKey": "dummy-junie-key-one",
            }
        },
    )
    monkeypatch.setattr(
        PROFILES,
        "litellm_catalogue_models",
        lambda *_: {
            "ollama-cloud/llama:cloud",
            "ollama-cloud/qwen:cloud",
            "omlx/local/model",
            "omlx/local/faster",
            "omlx/pool/model",
            "omlx/pool/faster",
            "omlx/pool/last",
            "openai/gpt-faster",
            "openai/gpt-present",
            "opencode/free-present",
            "openai/custom/alias",
        },
    )
    monkeypatch.setattr(
        PROFILES,
        "append_pool_profile_specs",
        lambda specs: specs.extend(
            [
                (
                    "local-omlx-pool",
                    "omlx/pool/model",
                    "omlx/pool/faster",
                    "omlx",
                    "omlx",
                ),
                ("local-omlx-final", "omlx/pool/last", "", "omlx", "omlx"),
                (
                    "local-omlx-missing-fast",
                    "omlx/pool/model",
                    "omlx/pool/missing",
                    "omlx",
                    "omlx",
                ),
            ]
        ),
    )

    PROFILES.main()

    cloud = json.loads((target / "pro.json").read_text())
    assert cloud["id"] == "ollama-cloud/llama:cloud"
    assert cloud["fasterModel"]["id"] == "ollama-cloud/qwen:cloud"
    assert (target / "pro.json").stat().st_mode & 0o777 == 0o600
    local = json.loads((target / "local-pro.json").read_text())
    assert local["id"] == "omlx/local/model"
    assert local["fasterModel"]["id"] == "omlx/local/faster"
    assert (target / "local-pro.json").stat().st_mode & 0o777 == 0o600
    assert local["baseUrl"] == "http://127.0.0.1:4000/v1"
    assert "baseUrl" not in local["fasterModel"]
    assert "apiKey" not in local["fasterModel"]
    pool = json.loads((target / "local-omlx-pool.json").read_text())
    assert pool["id"] == "omlx/pool/model"
    assert pool["fasterModel"]["id"] == "omlx/pool/faster"
    final = json.loads((target / "local-omlx-final.json").read_text())
    assert final["id"] == "omlx/pool/last"
    assert "fasterModel" not in final
    missing_fast = json.loads((target / "local-omlx-missing-fast.json").read_text())
    assert missing_fast["id"] == "omlx/pool/model"
    assert "fasterModel" not in missing_fast
    assert not (target / "plus.json").exists()
    free = json.loads((target / "free.json").read_text())
    assert free["id"] == "opencode/free-present"
    assert "fasterModel" not in free
    meridian = json.loads((target / "meridian.json").read_text())
    assert meridian["id"] == "claude-sonnet"
    assert meridian["fasterModel"]["id"] == "openai/gpt-present"
    assert meridian["fasterModel"]["baseUrl"] == "http://127.0.0.1:4000/v1"
    assert (
        json.loads((target / "litellm-openai-custom.json").read_text())["id"]
        == "openai/custom/alias"
    )

    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    PROFILES.main()
    direct_cloud = json.loads((target / "pro.json").read_text())
    assert direct_cloud["id"] == "llama:cloud"
    assert direct_cloud["baseUrl"] == "https://direct.example/ollama-cloud/v1"


def test_unknown_catalogue_omits_tiers_but_preserves_named_and_user_profiles(
    harness, monkeypatch
):
    _, target, _, groups_path = harness
    target.mkdir(parents=True)
    user_profile = target / "custom-user.json"
    user_profile.write_text('{"id":"user"}', encoding="utf-8")
    config = json.loads(groups_path.read_text())
    config["groups"] = {
        "openai-custom": {"provider": "openai", "primaryModel": "custom/alias"},
        "litellm-openai-custom": {
            "provider": "litellm",
            "primaryModel": "openai/custom/alias",
        },
    }
    groups_path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(
        PROFILES.tier_registry, "load_registry", lambda: {"presets": {"pro": {}}}
    )
    monkeypatch.setattr(
        PROFILES.tier_registry, "uses_local_placeholders", lambda *_: False
    )
    monkeypatch.setattr(
        PROFILES.tier_registry,
        "materialize_role_models",
        lambda *_: {"orchestrator": "openai/gpt", "librarian": ""},
    )
    monkeypatch.setattr(PROFILES, "resolve_engine", lambda *_: None)
    monkeypatch.setattr(
        PROFILES,
        "build_provider_configs",
        lambda *_: {
            "litellm": {
                "baseUrl": "http://127.0.0.1:4000/v1",
                "apiType": "OpenAICompletion",
                "apiKey": "dummy-junie-key-one",
            },
            "openai": {
                "baseUrl": "https://direct.example/v1",
                "apiType": "OpenAICompletion",
                "apiKey": "${OPENAI_API_KEY}",
            },
        },
    )
    monkeypatch.setattr(PROFILES, "litellm_catalogue_models", lambda *_: None)

    PROFILES.main()

    assert not (target / "pro.json").exists()
    assert (
        json.loads((target / "litellm-openai-custom.json").read_text())["id"]
        == "openai/custom/alias"
    )
    assert json.loads(user_profile.read_text())["id"] == "user"


def test_missing_key_removes_only_generated_litellm_profile_and_keeps_user_json(
    harness, monkeypatch
):
    _, target, key_file, _ = harness
    target.mkdir(parents=True)
    target.chmod(0o700)
    (target / "litellm-openai-main.json").write_text("stale", encoding="utf-8")
    (target / "openai-main.json").write_text("stale direct", encoding="utf-8")
    (target / PROFILES.PROFILE_MANIFEST).write_text(
        json.dumps(["litellm-openai-main", "openai-main"]), encoding="utf-8"
    )
    (target / PROFILES.PROFILE_MANIFEST).chmod(0o600)
    (target / "manual.json").write_text('{"user": true}', encoding="utf-8")
    key_file.unlink()

    PROFILES.main()

    assert not (target / "litellm-openai-main.json").exists()
    assert not (target / "openai-main.json").exists()
    assert (target / "manual.json").read_text(encoding="utf-8") == '{"user": true}'


def test_gate_on_with_invalid_litellm_endpoint_omits_direct_groups_but_keeps_meridian(
    harness, monkeypatch
):
    _, target, _, groups_path = harness
    config = json.loads(groups_path.read_text())
    config["providers"]["litellm"]["baseUrl"] = "https://invalid.example/v1"
    config["providers"]["meridian"] = {
        "baseUrl": "http://127.0.0.1:3456/v1/responses",
        "apiType": "OpenAIResponses",
        "apiKeyEnv": "MERIDIAN_API_KEY",
    }
    config["groups"]["meridian-opus"] = {
        "provider": "meridian",
        "primaryModel": "claude-opus",
    }
    groups_path.write_text(json.dumps(config), encoding="utf-8")

    PROFILES.main()

    assert not (target / "openai-main.json").exists()
    assert not (target / "openai-faster.json").exists()
    assert not any(target.glob("litellm-*.json"))
    assert (
        json.loads((target / "meridian-opus.json").read_text())["id"] == "claude-opus"
    )


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
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")

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
