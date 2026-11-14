import pytest
import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "configure-claude-3p-profiles.py"
SPEC = importlib.util.spec_from_file_location("claude_3p_profiles", SCRIPT)
profiles = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profiles)


def registry():
    return {
        "presets": {
            "cloud": {
                role: {"model": f"gateway/{role}"}
                for role in ("oracle", "orchestrator", "designer", "librarian")
            },
            "local": {
                role: {"model": f"ollama/{role}"}
                for role in ("oracle", "orchestrator", "designer", "librarian")
            },
        }
    }


def test_profile_mapping_cloud_and_local():
    roles = ("oracle", "orchestrator", "designer", "librarian")
    served = {
        f"{provider}/{role}"
        for provider in ("gateway", "ollama")
        for role in roles
    }
    served.update(
        f"anthropic/claude-{preset}-{role}"
        for preset in ("cloud", "local")
        for role in roles
    )
    desired = profiles.build_profiles(registry(), served)
    assert [item["behavesAs"] for item in desired["cloud"]["inferenceModels"]] == [
        "fable",
        "opus",
        "sonnet",
        "haiku",
    ]
    local_oracle = next(
        item
        for item in desired["local"]["inferenceModels"]
        if item["behavesAs"] == "fable"
    )
    assert local_oracle["name"] == "anthropic/claude-local-oracle"
    assert desired["cloud"]["inferenceModels"][0]["name"] == (
        "anthropic/claude-cloud-oracle"
    )
    assert desired["cloud"]["inferenceModels"][0]["labelOverride"] == "gateway/oracle"


def test_unchanged_profile_is_not_rewritten_and_mode_private(tmp_path):
    target = tmp_path / "profile.json"
    content = '{"ok": true}\n'
    assert profiles._atomic_write(target, content)
    assert target.stat().st_mode & 0o777 == 0o600
    before = target.stat().st_mtime_ns
    assert not profiles._atomic_write(target, content)
    assert target.stat().st_mtime_ns == before


def test_unserved_model_refuses_all_profiles():
    result = profiles.build_profiles(registry(), {"gateway/oracle"})
    assert set(result) == set()


def test_missing_new_bridge_alias_refuses_even_when_role_targets_are_served():
    targets = {
        f"{provider}/{role}"
        for provider in ("gateway", "ollama")
        for role in ("oracle", "orchestrator", "designer", "librarian")
    }
    assert profiles.build_profiles(registry(), targets) == {}


def test_key_file_mode_contains_key_only_in_credential_field(tmp_path):
    key = tmp_path / "key"
    key.write_text("sk-synthetic-only\n", encoding="utf-8")
    key.chmod(0o600)
    entries = profiles.build_profiles(registry(), None, profiles.read_private_key(key))
    entry = json.dumps(entries["cloud"])
    assert "sk-synthetic-only" in entry
    assert entry.count("sk-synthetic-only") == 1
    assert entries["cloud"]["inferenceGatewayApiKey"] == "sk-synthetic-only"


def test_no_key_mode_omits_credential():
    entry = profiles.build_profiles(registry(), None)["cloud"]
    assert "inferenceGatewayApiKey" not in entry


def test_served_aliases_are_exact_model_names_not_backend_ids(tmp_path):
    path = tmp_path / "litellm.yaml"
    path.write_text(
        "model_list:\n"
        "  - model_name: ollama/glm-5.3-flash:cloud\n"
        "    litellm_params:\n"
        "      model: ollama/glm-5.3-flash:cloud\n"
        "  - model_name: Qwen3.8-27B-4bit\n"
        "    litellm_params:\n"
        "      model: ollama/qwen3.8:27b-mlx\n",
        encoding="utf-8",
    )
    assert profiles._read_served_ids(path) == {
        "ollama/glm-5.3-flash:cloud",
        "Qwen3.8-27B-4bit",
    }
    assert "qwen3.8:27b-mlx" not in profiles._read_served_ids(path)


def _run_main(monkeypatch, tmp_path, served, argv_extra):
    monkeypatch.setenv("DOTFILES_RUN_OPENCODE_SETUP", "1")
    def served_with_bridges(_path):
        selected = set(served)
        for preset, provider in (("cloud", "gateway"), ("local", "ollama")):
            for role in ("oracle", "orchestrator", "designer", "librarian"):
                if f"{provider}/{role}" in selected:
                    selected.add(f"anthropic/claude-{preset}-{role}")
        return selected

    monkeypatch.setattr(profiles, "_read_served_ids", served_with_bridges)
    monkeypatch.setattr(profiles, "_local_category_models", lambda *_a, **_k: {})
    monkeypatch.setattr(profiles.tier_registry, "load_registry", registry)
    source = tmp_path / "configLibrary"
    argv = ["--source-dir", str(source), "--dry-run", *argv_extra]
    return source, profiles.main(argv)


def test_partial_skip_exits_1_without_allow_partial(monkeypatch, tmp_path):
    served = {
        "gateway/oracle",
        "gateway/orchestrator",
        "gateway/designer",
        "gateway/librarian",
    }
    _, code = _run_main(monkeypatch, tmp_path, served, [])
    assert code == 1  # unserved local preset is a truthful partial failure


def test_allow_partial_named_acceptance_only(monkeypatch, tmp_path):
    served = {
        "gateway/oracle",
        "gateway/orchestrator",
        "gateway/designer",
        "gateway/librarian",
        "ollama/oracle",
        "ollama/orchestrator",
        "ollama/designer",
    }
    source, code = _run_main(
        monkeypatch, tmp_path, served, ["--allow-partial", "LiteLLM-local"]
    )
    assert code == 0  # the single skip is explicitly named and accepted
    _, code = _run_main(monkeypatch, tmp_path, served, [])
    assert code == 1  # the same skip without acceptance refuses the partial set
    served.add("ollama/librarian")
    _, code = _run_main(monkeypatch, tmp_path, served, [])
    assert code == 0  # complete served registry delivers every profile
    # Dry-run must never have written, even with an accepted skip.
    assert not (source / "_meta.json").exists()


def test_index_update_registers_unique_ids(tmp_path):
    source = tmp_path / "configLibrary"
    source.mkdir()
    index_path = source / "_meta.json"
    index_path.write_text(
        json.dumps({"appliedId": None, "entries": [{"id": "x", "name": "oMLX"}]}),
        encoding="utf-8",
    )
    desired = profiles.build_profiles(registry(), None)
    index_path, content = profiles._update_index(source, desired)
    index = json.loads(content)
    names = {entry["name"] for entry in index["entries"]}
    assert names == {"oMLX", "LiteLLM-cloud", "LiteLLM-local"}
    ids = {
        entry["id"]
        for entry in index["entries"]
        if entry["name"].startswith("LiteLLM-")
    }
    assert len(ids) == len(names) - 1 and all(len(item) == 36 for item in ids)


def test_existing_foreign_profile_is_never_overwritten(tmp_path):
    target = tmp_path / "foreign-profile.json"
    target.write_text('{"deploymentDisplayName": "Owner profile"}', encoding="utf-8")
    target.chmod(0o600)
    entry = profiles.build_profiles(registry(), None)["cloud"]
    with pytest.raises(ValueError, match="not a recognised managed profile"):
        profiles._validate_owned_profile(target, entry)
    assert "Owner profile" in target.read_text(encoding="utf-8")


def test_private_backup_is_created_exclusively_before_replacement(tmp_path):
    target = tmp_path / "profile.json"
    target.write_text('{"old": true}', encoding="utf-8")
    target.chmod(0o600)
    assert profiles._atomic_write(target, '{"new": true}', backup=True)
    backup = target.with_name("profile.json.bak")
    assert backup.stat().st_mode & 0o777 == 0o600
    assert backup.read_text() == '{"old": true}'
    assert target.read_text() == '{"new": true}'
    with pytest.raises(ValueError, match="backup already exists"):
        profiles._atomic_write(target, '{"next": true}', backup=True)
    assert target.read_text() == '{"new": true}'


def test_non_private_existing_file_and_foreign_index_are_refused(tmp_path):
    target = tmp_path / "profile.json"
    target.write_text('{"old": true}', encoding="utf-8")
    target.chmod(0o644)
    with pytest.raises(ValueError, match="non-private"):
        profiles._atomic_write(target, '{"new": true}')
    assert target.read_text() == '{"old": true}'

    source = tmp_path / "configLibrary"
    source.mkdir()
    index = source / "_meta.json"
    index.write_text(
        json.dumps({"entries": [{"name": "LiteLLM-cloud", "id": "foreign-owner-id"}]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unmanaged profile index"):
        profiles._update_index(source, profiles.build_profiles(registry(), None))


def test_skipped_owned_profile_index_is_not_advertised(tmp_path):
    source = tmp_path / "configLibrary"
    source.mkdir()
    entry_id = str(
        profiles.uuid.uuid5(profiles.uuid.NAMESPACE_URL, "dotfiles-claude-3p-local")
    )
    (source / "_meta.json").write_text(
        json.dumps(
            {
                "entries": [
                    {"name": "LiteLLM-local", "id": entry_id},
                    {"name": "Unrelated", "id": "owner-retained"},
                ]
            }
        ),
        encoding="utf-8",
    )
    _path, content = profiles._update_index(
        source, {"cloud": profiles.build_profiles(registry(), None)["cloud"]}
    )
    names = [entry["name"] for entry in json.loads(content)["entries"]]
    assert names == ["LiteLLM-cloud", "Unrelated"]


def test_regeneration_preserves_app_sealed_key_and_unmanaged_metadata(tmp_path):
    desired = profiles.build_profiles(registry(), None)["cloud"]
    target = tmp_path / "managed-profile.json"
    original = {
        **desired,
        "inferenceGatewayApiKey": "synthetic-app-sealed-blob",
        "appCustomMetadata": {"owner": "preserve"},
    }
    target.write_text(json.dumps(original), encoding="utf-8")
    target.chmod(0o600)
    restored = profiles._merge_app_owned_fields(target, desired)
    assert restored["inferenceGatewayApiKey"] == "synthetic-app-sealed-blob"
    assert restored["appCustomMetadata"] == {"owner": "preserve"}
    assert restored["inferenceModels"] == desired["inferenceModels"]

    explicitly_rekeyed = {
        **desired,
        "inferenceGatewayApiKey": "sk-synthetic-replacement",
    }
    replaced = profiles._merge_app_owned_fields(target, explicitly_rekeyed)
    assert replaced["inferenceGatewayApiKey"] == "sk-synthetic-replacement"
    assert replaced["appCustomMetadata"] == {"owner": "preserve"}
