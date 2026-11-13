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
    desired = profiles.build_profiles(
        registry(),
        {
            f"{provider}/{role}"
            for provider in ("gateway", "ollama")
            for role in ("oracle", "orchestrator", "designer", "librarian")
        },
    )
    assert [item["behavesAs"] for item in desired["cloud"]["inferenceModels"]] == [
        "fable",
        "opus",
        "sonnet",
        "haiku",
    ]
    assert desired["local"]["inferenceModels"][0]["name"] == "ollama/oracle"


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
    monkeypatch.setattr(profiles, "_read_served_ids", lambda _path: set(served))
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
