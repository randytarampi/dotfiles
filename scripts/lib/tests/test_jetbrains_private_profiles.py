import importlib.util
import hashlib
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
                    "openai-main": {
                        "provider": "openai",
                        "primaryModel": "gpt-main",
                    },
                    "openai-faster": {
                        "provider": "openai",
                        "primaryModel": "gpt-faster",
                        "fasterModel": "junie-fast",
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
        PROFILES,
        "litellm_catalogue_models",
        lambda *_args: set(),
    )
    import model_stamp

    monkeypatch.setattr(
        model_stamp, "STAMP_PATH", home / ".local/share/dotfiles/model-sync-stamp"
    )
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


def test_disabled_free_group_is_not_generated(harness, monkeypatch):
    _, target, _, groups_path = harness
    cfg = json.loads(groups_path.read_text())
    cfg["groups"] = {"free": {"provider": "openai", "primaryModel": "frozen-model"}}
    groups_path.write_text(json.dumps(cfg))
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    monkeypatch.setattr(
        PROFILES.tier_registry,
        "load_registry",
        lambda: {
            "presets": {
                "free": {
                    "orchestrator": {"model": "openai/registry-model"},
                    "librarian": {"model": "openai/registry-faster"},
                }
            }
        },
    )
    PROFILES.main()
    assert not (target / "free.json").exists()


def test_generated_litellm_profiles_are_private_and_share_cli_ide_directory(
    harness, caplog, monkeypatch
):
    home, target, key_file, _ = harness
    monkeypatch.setattr(
        PROFILES,
        "litellm_catalogue_models",
        lambda *_: {
            "openai/gpt-main",
            "openai/gpt-faster",
            "openai/junie-fast",
        },
    )
    PROFILES.main()
    primary = json.loads((target / "litellm-openai-main.json").read_text())
    nested = json.loads((target / "litellm-openai-faster.json").read_text())
    assert primary["apiKey"] == "dummy-junie-key-one"
    assert nested["fasterModel"]["id"] == "openai/junie-fast"
    assert "apiKey" not in nested["fasterModel"]
    assert "baseUrl" not in nested["fasterModel"]
    assert nested["fasterModel"]["temperature"] is not None
    assert nested["baseUrl"] == "http://127.0.0.1:4000/v1/chat/completions"
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


def test_manual_profile_collision_is_preserved(harness):
    _, target, _, _ = harness
    target.mkdir(parents=True)
    manual = target / "litellm-openai-main.json"
    manual.write_text('{"manual":true}', encoding="utf-8")
    PROFILES.main()
    assert manual.read_text(encoding="utf-8") == '{"manual":true}'


def test_modified_managed_profile_refuses_overwrite(harness, monkeypatch):
    _, target, _, _ = harness
    monkeypatch.setattr(
        PROFILES, "litellm_catalogue_models", lambda *_: {"openai/gpt-main"}
    )
    PROFILES.main()
    profile = target / "litellm-openai-main.json"
    profile.write_text('{"edited":true}', encoding="utf-8")
    with pytest.raises(SystemExit):
        PROFILES.main()
    assert profile.read_text(encoding="utf-8") == '{"edited":true}'


def test_manifest_digests_cover_generated_outputs(harness, monkeypatch):
    home, target, _, _ = harness
    monkeypatch.setattr(
        PROFILES, "litellm_catalogue_models", lambda *_: {"openai/gpt-main"}
    )
    PROFILES.main()
    data = json.loads(
        (home / ".local/share/dotfiles/jetbrains-profiles-manifest.json").read_text()
    )
    assert (
        data["profiles"]["litellm-openai-main"]
        == hashlib.sha256(
            (target / "litellm-openai-main.json").read_bytes()
        ).hexdigest()
    )


def test_dotted_profile_name_cleanup_removes_only_digest_owned_file(
    harness, monkeypatch
):
    home, target, _, groups_path = harness
    config = json.loads(groups_path.read_text())
    config["groups"] = {
        "custom.json": {"provider": "openai", "primaryModel": "gpt-custom"}
    }
    groups_path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    monkeypatch.setattr(
        PROFILES,
        "build_provider_configs",
        lambda *_: {
            "openai": {
                "baseUrl": "https://direct.example/v1",
                "apiType": "OpenAICompletion",
                "apiKey": "${OPENAI_API_KEY}",
            }
        },
    )

    PROFILES.main()
    generated = target / "custom.json.json"
    assert generated.is_file()
    user_collision = target / "custom.json"
    user_collision.write_text('{"manual":true}', encoding="utf-8")

    config["groups"] = {}
    groups_path.write_text(json.dumps(config), encoding="utf-8")
    PROFILES.main()

    assert not generated.exists()
    assert user_collision.read_text(encoding="utf-8") == '{"manual":true}'
    ledger = json.loads(
        (home / ".local/share/dotfiles/jetbrains-profiles-manifest.json").read_text()
    )
    assert "custom.json" not in ledger["profiles"]


def test_empty_shared_groups_still_cleanup_and_generate_enabled_tiers(
    harness, monkeypatch
):
    home, target, _, groups_path = harness
    config = json.loads(groups_path.read_text(encoding="utf-8"))
    config["groups"] = {
        "enabled-group": {"provider": "openai", "primaryModel": "old"},
        "stale-group": {"provider": "openai", "primaryModel": "stale"},
    }
    config["sharedPresets"] = {"free": "free", "pro": "pro"}
    groups_path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    monkeypatch.setattr(
        PROFILES,
        "build_provider_configs",
        lambda *_: {
            "openai": {
                "baseUrl": "https://direct.example/v1",
                "apiType": "OpenAICompletion",
                "apiKey": "${OPENAI_API_KEY}",
            }
        },
    )
    monkeypatch.setattr(
        PROFILES.tier_registry,
        "load_registry",
        lambda: {"presets": {"enabled-tier": {}, "pro": {}}},
    )
    monkeypatch.setattr(
        PROFILES.tier_registry,
        "uses_local_placeholders",
        lambda *_: False,
    )
    monkeypatch.setattr(
        PROFILES.tier_registry,
        "materialize_role_models",
        lambda *_args, **_kwargs: {
            "orchestrator": "openai/gpt-main",
            "librarian": "openai/gpt-faster",
        },
    )
    PROFILES.main()
    owned = target / "stale-group.json"
    assert owned.is_file()
    manifest = home / ".local/share/dotfiles/jetbrains-profiles-manifest.json"
    before = json.loads(manifest.read_text(encoding="utf-8"))["profiles"]["stale-group"]
    assert hashlib.sha256(owned.read_bytes()).hexdigest() == before
    enabled = target / "enabled-group.json"
    assert enabled.is_file()

    # Disable shared groups; the digest-owned profile should be cleaned up.
    config["groups"] = {}
    groups_path.write_text(json.dumps(config), encoding="utf-8")
    PROFILES.main()
    assert not owned.exists()
    assert (
        "stale-group"
        not in json.loads(manifest.read_text(encoding="utf-8"))["profiles"]
    )
    assert (target / "pro.json").is_file()
    assert (target / "enabled-tier.json").is_file()


def test_empty_shared_groups_cleanup_owned_free_but_preserve_manual_collision(
    harness, monkeypatch
):
    home, target, _, groups_path = harness
    config = json.loads(groups_path.read_text(encoding="utf-8"))
    config["groups"] = {}
    config["sharedPresets"] = {"free": "free"}
    groups_path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    monkeypatch.setattr(
        PROFILES,
        "build_provider_configs",
        lambda *_: {
            "openai": {
                "baseUrl": "https://direct.example/v1",
                "apiType": "OpenAICompletion",
                "apiKey": "${OPENAI_API_KEY}",
            }
        },
    )
    monkeypatch.setattr(
        PROFILES.tier_registry,
        "load_registry",
        lambda: {"presets": {"enabled-tier": {}}},
    )
    monkeypatch.setattr(
        PROFILES.tier_registry, "uses_local_placeholders", lambda *_: False
    )
    monkeypatch.setattr(
        PROFILES.tier_registry,
        "materialize_role_models",
        lambda *_args, **_kwargs: {
            "orchestrator": "openai/gpt-main",
            "librarian": "openai/gpt-faster",
        },
    )
    profile = target / "free.json"
    target.mkdir(parents=True, exist_ok=True)
    profile.write_text('{"generated":true}', encoding="utf-8")
    digest = hashlib.sha256(profile.read_bytes()).hexdigest()
    PROFILES.atomic_write_manifest(target, {"free": digest})
    PROFILES.main()
    assert not profile.exists()
    ledger = home / ".local/share/dotfiles/jetbrains-profiles-manifest.json"
    assert "free" not in json.loads(ledger.read_text(encoding="utf-8"))["profiles"]
    assert (target / "enabled-tier.json").is_file()
    # An edited file with the same prior ownership digest is not ours to delete.
    profile.write_text('{"manual":true}', encoding="utf-8")
    manual_bytes = profile.read_bytes()
    PROFILES.atomic_write_manifest(target, {"free": digest})
    with pytest.raises(SystemExit) as error:
        PROFILES.main()
    assert error.value.code == 1
    assert profile.read_bytes() == manual_bytes


@pytest.mark.parametrize("ledger_kind", ["absent", "untrusted"])
def test_empty_shared_groups_fail_closed_without_trusted_manifest(
    harness, monkeypatch, ledger_kind
):
    home, target, _, groups_path = harness
    config = json.loads(groups_path.read_text(encoding="utf-8"))
    config["groups"] = {
        "enabled-group": {"provider": "openai", "primaryModel": "frozen"}
    }
    config["sharedPresets"] = {"free": "free"}
    groups_path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    monkeypatch.setattr(
        PROFILES,
        "build_provider_configs",
        lambda *_: {
            "openai": {
                "baseUrl": "https://direct.example/v1",
                "apiType": "OpenAICompletion",
                "apiKey": "${OPENAI_API_KEY}",
            }
        },
    )
    monkeypatch.setattr(
        PROFILES.tier_registry,
        "load_registry",
        lambda: {"presets": {"enabled-tier": {}}},
    )
    monkeypatch.setattr(
        PROFILES.tier_registry, "uses_local_placeholders", lambda *_: False
    )
    monkeypatch.setattr(
        PROFILES.tier_registry,
        "materialize_role_models",
        lambda *_args, **_kwargs: {
            "orchestrator": "openai/gpt-main",
            "librarian": "openai/gpt-faster",
        },
    )
    PROFILES.main()
    profile = target / "enabled-group.json"
    assert profile.is_file()
    original = profile.read_bytes()
    # Seed an unmanaged would-be stale path: without a trusted ledger the
    # generator cannot infer ownership or delete it.
    untracked = target / "free.json"
    untracked.write_text('{"manual":true}', encoding="utf-8")
    original_untracked = untracked.read_bytes()
    ledger = home / ".local/share/dotfiles/jetbrains-profiles-manifest.json"
    if ledger_kind == "untrusted":
        ledger.write_text("{}\n", encoding="utf-8")
        ledger.chmod(0o600)
    else:
        ledger.unlink()

    config["groups"] = {}
    groups_path.write_text(json.dumps(config), encoding="utf-8")
    PROFILES.main()
    assert profile.read_bytes() == original
    assert profile.is_file()
    assert untracked.read_bytes() == original_untracked
    assert (target / "enabled-tier.json").is_file()


def test_compatibility_names_remain_selectable_after_generation(harness, monkeypatch):
    _, target, _, groups_path = harness
    config = json.loads(groups_path.read_text(encoding="utf-8"))
    config["compatibility"] = {"legacy-main": "openai-main"}
    groups_path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-openai-key")
    config["groups"]["openai-main"].update(
        {"fasterModel": "gpt-fast", "fasterProvider": "openai"}
    )
    groups_path.write_text(json.dumps(config), encoding="utf-8")

    PROFILES.main()

    canonical = json.loads((target / "openai-main.json").read_text(encoding="utf-8"))
    compatibility = json.loads(
        (target / "legacy-main.json").read_text(encoding="utf-8")
    )
    for field in ("primaryModel", "fasterModel", "baseUrl", "apiType", "apiKey"):
        assert compatibility[field] == canonical[field]

    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setattr(
        PROFILES,
        "litellm_catalogue_models",
        lambda *_: {"openai/gpt-main", "openai/gpt-fast"},
    )

    PROFILES.main()

    canonical_twin_path = target / "litellm-openai-main.json"
    alias_twin_path = target / "litellm-legacy-main.json"
    assert canonical_twin_path.is_file()
    assert alias_twin_path.is_file()
    canonical_twin = json.loads(canonical_twin_path.read_text(encoding="utf-8"))
    alias_twin = json.loads(alias_twin_path.read_text(encoding="utf-8"))
    for field in ("primaryModel", "fasterModel", "baseUrl", "apiType", "apiKey"):
        assert alias_twin[field] == canonical_twin[field]


def test_crash_after_profile_overwrite_preserves_manual_files_and_refuses_retry(
    harness, monkeypatch
):
    home, target, _, _ = harness
    monkeypatch.setattr(
        PROFILES,
        "litellm_catalogue_models",
        lambda *_: {"openai/gpt-main", "openai/gpt-main-new"},
    )
    monkeypatch.setattr(
        PROFILES,
        "build_provider_configs",
        lambda *_: {
            "openai": {
                "baseUrl": "https://direct.example/v1",
                "apiType": "OpenAICompletion",
                "apiKey": "${OPENAI_API_KEY}",
            },
            "litellm": {
                "baseUrl": "http://127.0.0.1:4000/v1",
                "apiType": "OpenAICompletion",
                "apiKey": "dummy-junie-key-one",
            },
        },
    )
    monkeypatch.setattr(
        PROFILES.tier_registry,
        "load_registry",
        lambda: {"presets": {"enabled-tier": {}}},
    )
    monkeypatch.setattr(
        PROFILES.tier_registry, "uses_local_placeholders", lambda *_: False
    )
    PROFILES.main()
    profile = target / "litellm-openai-main.json"
    manual = target / "manual.json"
    old_profile_bytes = profile.read_bytes()
    manual.write_text('{"manual":true}', encoding="utf-8")
    old_ledger = home / ".local/share/dotfiles/jetbrains-profiles-manifest.json"
    old_ledger_bytes = old_ledger.read_bytes()

    original_write = PROFILES.atomic_write_manifest

    def fail_manifest(*_args):
        raise OSError("injected manifest publication failure")

    monkeypatch.setattr(PROFILES, "atomic_write_manifest", fail_manifest)
    # A changed generated profile reaches the deliberate interruption window.
    config_path = Path(sys.argv[sys.argv.index("--groups-json") + 1])
    config = json.loads(config_path.read_text())
    config["groups"]["openai-main"]["primaryModel"] = "gpt-main-new"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(OSError, match="injected manifest publication failure"):
        PROFILES.main()
    assert manual.read_text(encoding="utf-8") == '{"manual":true}'
    assert old_ledger.read_bytes() == old_ledger_bytes
    assert profile.read_bytes() != old_profile_bytes
    interrupted_profile_bytes = profile.read_bytes()

    monkeypatch.setattr(PROFILES, "atomic_write_manifest", original_write)
    with pytest.raises(SystemExit):
        PROFILES.main()
    assert manual.read_text(encoding="utf-8") == '{"manual":true}'
    assert profile.read_bytes() == interrupted_profile_bytes
    assert old_ledger.read_bytes() == old_ledger_bytes


def test_unknown_catalogue_preserves_owned_profile_and_ledger(harness, monkeypatch):
    home, target, _, _ = harness
    monkeypatch.setattr(
        PROFILES, "litellm_catalogue_models", lambda *_: {"openai/gpt-main"}
    )
    PROFILES.main()
    profile = target / "litellm-openai-main.json"
    ledger = home / ".local/share/dotfiles/jetbrains-profiles-manifest.json"
    before = (profile.read_bytes(), profile.stat().st_mtime_ns, ledger.read_bytes())
    monkeypatch.setattr(PROFILES, "litellm_catalogue_models", lambda *_: None)
    PROFILES.main()
    assert (
        profile.read_bytes(),
        profile.stat().st_mtime_ns,
        ledger.read_bytes(),
    ) == before


def test_modified_owned_profile_refuses_before_other_profile_writes(
    harness, monkeypatch
):
    _, target, _, _ = harness
    monkeypatch.setattr(
        PROFILES,
        "litellm_catalogue_models",
        lambda *_: {"openai/gpt-main", "openai/gpt-faster", "openai/junie-fast"},
    )
    PROFILES.main()
    edited = target / "litellm-openai-faster.json"
    edited.write_bytes(b"user edit")
    before = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in target.iterdir()
    }
    with pytest.raises(SystemExit):
        PROFILES.main()
    assert {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in target.iterdir()
    } == before


def test_manifest_cannot_transfer_ownership_to_another_directory(harness, tmp_path):
    _, target, _, _ = harness
    target.mkdir(parents=True)
    original = target / "same.json"
    original.write_bytes(b"identical")
    PROFILES.atomic_write_manifest(
        target, {"same": hashlib.sha256(b"identical").hexdigest()}
    )
    other = tmp_path / "manual-models"
    other.mkdir()
    manual = other / "same.json"
    manual.write_bytes(b"identical")
    with pytest.raises(ValueError, match="target directory mismatch"):
        PROFILES.read_profile_manifest(other)
    assert manual.read_bytes() == b"identical"


def test_handwritten_litellm_static_divergence_is_flagged_and_fail_closed(
    harness, caplog
):
    _, target, _, groups_path = harness
    config = json.loads(groups_path.read_text())
    config["groups"]["litellm-openai-main"] = {
        "provider": "litellm",
        "primaryModel": "openai/wrong-alias",
        "fasterModel": "google/some-model",
        "fasterProvider": "google",
    }
    groups_path.write_text(json.dumps(config), encoding="utf-8")
    derived = PROFILES.derive_litellm_groups(config["groups"])
    assert PROFILES.routed_group_violations(config["groups"], derived)
    PROFILES.main()
    assert "Derived Junie twin litellm-openai-main diverges from openai-main" in (
        caplog.text
    )
    assert not (target / "litellm-openai-main.json").exists()


def test_tier_and_pool_profiles_proxy_or_omit_exact_aliases(harness, monkeypatch):
    _, target, _, groups_path = harness
    target.mkdir(parents=True)
    (target / "plus.json").write_text('{"id":"stale-direct"}', encoding="utf-8")
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
        "local": ("omlx/local/model", "omlx/local/faster"),
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
            "meridian/claude-sonnet",
            "meridian/claude-opus",
            "openai/anthropic/claude-sonnet",
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
    local = json.loads((target / "local.json").read_text())
    assert local["id"] == "omlx/local/model"
    assert local["fasterModel"]["id"] == "omlx/local/faster"
    assert (target / "local.json").stat().st_mode & 0o777 == 0o600
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
    assert (target / "plus.json").read_text() == '{"id":"stale-direct"}'
    assert not (target / "free.json").exists()
    assert not (target / "litellm-free.json").exists()
    meridian = json.loads((target / "meridian.json").read_text())
    assert meridian["id"] == "meridian/claude-sonnet"
    assert meridian["fasterModel"]["id"] == "openai/gpt-present"
    assert "baseUrl" not in meridian["fasterModel"]
    assert (
        json.loads((target / "litellm-openai-custom.json").read_text())["id"]
        == "openai/custom/alias"
    )

    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    PROFILES.main()
    direct_cloud = json.loads((target / "pro.json").read_text())
    assert direct_cloud["id"] == "llama:cloud"
    assert direct_cloud["baseUrl"] == "https://direct.example/ollama-cloud/v1"


@pytest.mark.parametrize("legacy_data", [["openai-main"], ["manual"]])
def test_legacy_manifest_refuses_without_modifying_anything(harness, legacy_data):
    _, target, _, _ = harness
    target.mkdir(parents=True)
    legacy = target / PROFILES.PROFILE_MANIFEST
    legacy.write_text(json.dumps(legacy_data), encoding="utf-8")
    legacy.chmod(0o600)
    manual = target / "litellm-openai-main.json"
    manual.write_text('{"manual":true}', encoding="utf-8")
    before = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode)
        for path in target.iterdir()
    }

    with pytest.raises(SystemExit):
        PROFILES.main()

    assert {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode)
        for path in target.iterdir()
    } == before


def test_legacy_manifest_dry_run_reports_refusal_without_writes(harness, caplog):
    _, target, _, _ = harness
    target.mkdir(parents=True)
    legacy = target / PROFILES.PROFILE_MANIFEST
    legacy.write_text('["openai-main"]', encoding="utf-8")
    legacy.chmod(0o600)
    before = (legacy.read_bytes(), legacy.stat().st_mtime_ns, legacy.stat().st_mode)
    sys.argv.append("--dry-run")
    try:
        with pytest.raises(SystemExit):
            PROFILES.main()
    finally:
        sys.argv.pop()
    assert str(legacy) in caplog.text
    assert "back it up privately" in caplog.text
    assert (
        legacy.read_bytes(),
        legacy.stat().st_mtime_ns,
        legacy.stat().st_mode,
    ) == before


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
    assert not (target / "litellm-openai-custom.json").exists()
    assert json.loads(user_profile.read_text())["id"] == "user"


def test_missing_key_preserves_legacy_name_only_profiles_and_user_json(
    harness, monkeypatch
):
    _, target, key_file, _ = harness
    target.mkdir(parents=True)
    target.chmod(0o700)
    (target / "litellm-openai-main.json").write_text("stale", encoding="utf-8")
    (target / "openai-main.json").write_text("stale direct", encoding="utf-8")
    legacy = target / PROFILES.PROFILE_MANIFEST
    legacy.write_text(
        json.dumps(["litellm-openai-main", "openai-main"]), encoding="utf-8"
    )
    legacy.chmod(0o600)
    (target / "manual.json").write_text('{"user": true}', encoding="utf-8")
    key_file.unlink()

    before = legacy.read_bytes()
    with pytest.raises(SystemExit):
        PROFILES.main()

    assert legacy.read_bytes() == before
    assert (target / "litellm-openai-main.json").read_text() == "stale"
    assert (target / "openai-main.json").read_text() == "stale direct"
    assert (target / "manual.json").read_text(encoding="utf-8") == '{"user": true}'


def test_gate_on_with_invalid_litellm_endpoint_omits_gateway_groups(
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
    config["groups"]["anthropic-opus"] = {
        "provider": "meridian",
        "primaryModel": "claude-opus",
    }
    groups_path.write_text(json.dumps(config), encoding="utf-8")

    PROFILES.main()

    assert not (target / "openai-main.json").exists()
    assert not (target / "openai-faster.json").exists()
    assert not any(target.glob("litellm-*.json"))
    assert not (target / "anthropic-opus.json").exists()


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
    try:
        PROFILES.main()
    finally:
        sys.argv.pop()
    assert "dummy-junie-key-one" not in caplog.text
    assert "dummy-junie-key-one" not in sys.argv
    assert not target.exists()


def test_dry_run_preserves_existing_collision_and_directory_metadata(
    harness, monkeypatch, caplog
):
    _, target, _, _ = harness
    monkeypatch.setattr(
        PROFILES,
        "litellm_catalogue_models",
        lambda *_: {"openai/gpt-main", "openai/gpt-faster", "openai/junie-fast"},
    )
    target.mkdir(parents=True)
    target.chmod(0o700)
    manual = target / "litellm-openai-main.json"
    manual.write_text('{"manual":true}', encoding="utf-8")
    before = {
        path.name: (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)
        for path in target.iterdir()
    }
    directory_before = (target.stat().st_mode, target.stat().st_mtime_ns)
    sys.argv.append("--dry-run")
    try:
        PROFILES.main()
    finally:
        sys.argv.pop()
    assert (
        "Preserving unmanaged Junie profile collision: litellm-openai-main.json"
        in caplog.text
    )
    assert {
        path.name: (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)
        for path in target.iterdir()
    } == before
    assert (target.stat().st_mode, target.stat().st_mtime_ns) == directory_before


def test_dry_run_refuses_modified_owned_profile_without_changes(harness, monkeypatch):
    home, target, _, _ = harness
    monkeypatch.setattr(
        PROFILES, "litellm_catalogue_models", lambda *_: {"openai/gpt-main"}
    )
    PROFILES.main()
    managed = target / "litellm-openai-main.json"
    assert managed.exists()
    managed.write_text('{"edited":true}', encoding="utf-8")
    before = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in target.iterdir()
    }
    sys.argv.append("--dry-run")
    try:
        with pytest.raises(SystemExit):
            PROFILES.main()
    finally:
        sys.argv.pop()
    assert {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in target.iterdir()
    } == before


def test_gate_off_preserves_unowned_legacy_profiles(harness, monkeypatch):
    _, target, _, _ = harness
    target.mkdir(parents=True)
    target.chmod(0o700)
    (target / "litellm-openai-main.json").write_text("stale", encoding="utf-8")
    (target / "unrelated.json").write_text('{"user": true}', encoding="utf-8")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")

    PROFILES.main()

    assert (target / "litellm-openai-main.json").read_text() == "stale"
    assert (target / "unrelated.json").read_text(encoding="utf-8") == '{"user": true}'


def test_profile_symlink_is_rejected(harness):
    _, target, _, _ = harness
    target.mkdir(parents=True)
    target.chmod(0o700)
    outside = target.parent / "outside.json"
    outside.write_text("do-not-touch", encoding="utf-8")
    (target / "litellm-openai-main.json").symlink_to(outside)
    with pytest.raises(OSError, match="unsafe stale Junie profile path"):
        PROFILES.cleanup_profiles(target, {"litellm-openai-main"}, set(), set(), False)
    assert (target / "litellm-openai-main.json").is_symlink()
    assert outside.read_text(encoding="utf-8") == "do-not-touch"


def test_models_directory_symlink_is_rejected(harness):
    _, target, _, _ = harness
    outside = target.parent / "external-models"
    outside.mkdir()
    target.symlink_to(outside, target_is_directory=True)
    with pytest.raises(SystemExit):
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


def test_manifest_refuses_legacy_file_in_models_dir(harness):
    home, target, _, _ = harness
    target.mkdir(parents=True, exist_ok=True)
    legacy = target / PROFILES.PROFILE_MANIFEST
    legacy.write_text(json.dumps(["older-profile"]), encoding="utf-8")
    legacy.chmod(0o600)
    new_path = home / ".local/share/dotfiles/jetbrains-profiles-manifest.json"
    with pytest.raises(OSError, match="legacy Junie profile manifest"):
        PROFILES.read_profile_manifest(target)
    assert legacy.exists()
    digests = {"fresh-profile": hashlib.sha256(b"profile").hexdigest()}
    PROFILES.atomic_write_manifest(target, digests)
    assert json.loads(new_path.read_text(encoding="utf-8"))["profiles"] == digests
    assert legacy.exists()
