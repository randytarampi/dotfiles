import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/configure-opencode.py"
SPEC = importlib.util.spec_from_file_location(
    "configure_opencode_cloud_wire_test", SCRIPT
)
CONFIGURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONFIGURE)
PI_SCRIPT = ROOT / "scripts/configure-pi.py"
PI_SPEC = importlib.util.spec_from_file_location(
    "configure_pi_cloud_canary_test", PI_SCRIPT
)
PI = importlib.util.module_from_spec(PI_SPEC)
PI_SPEC.loader.exec_module(PI)

PHANTOM_IDS = {
    "gemma4:31b:cloud",
    "gpt-oss:120b:cloud",
    "gpt-oss:20b:cloud",
    "mistral-large-3:675b:cloud",
    "nemotron-3-nano:30b:cloud",
}
INSTALLED_STUBS = [
    "gemma4:31b-cloud",
    "gpt-oss:120b-cloud",
    "gpt-oss:20b-cloud",
    "mistral-large-3:675b-cloud",
    "nemotron-3-nano:30b-cloud",
]


@pytest.fixture
def generate_config(tmp_path, monkeypatch):
    config_dir = tmp_path / "opencode"
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OPENCODE_DIR", str(config_dir))
    monkeypatch.setenv("DOTFILES_USE_LOCAL_OLLAMA", "false")
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.delenv("DOTFILES_RUN_LITELLM_SETUP", raising=False)
    monkeypatch.delenv("DOTFILES_OPENCODE_USE_LITELLM", raising=False)
    monkeypatch.setattr(CONFIGURE, "load_env", lambda *_args: True)
    monkeypatch.setattr(CONFIGURE, "list_local_ollama_models", lambda: [])
    monkeypatch.setattr(CONFIGURE, "check_ollama_daemon", lambda: (True, True))
    monkeypatch.setattr(CONFIGURE, "list_cloud_ollama_models", lambda: INSTALLED_STUBS)
    monkeypatch.setattr(CONFIGURE, "fetch_models_dev", lambda: {})
    monkeypatch.setattr(CONFIGURE, "get_ollama_context_length", lambda _name: None)
    monkeypatch.setattr(CONFIGURE, "get_ollama_modalities", lambda *_args: None)
    monkeypatch.setattr(
        CONFIGURE,
        "active_plugin_specs",
        lambda: ["plugin", "dcp", "@plannotator/opencode@test"],
    )
    monkeypatch.setattr(CONFIGURE, "plugin_specs", lambda: [])
    monkeypatch.setattr(CONFIGURE, "load_domains", lambda: {})
    monkeypatch.setattr(CONFIGURE, "build_opencode_server_config", lambda: {})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--preset",
            "pro-plus",
            "--skip",
            "mcps,acp-agents,tier,voice,dcp",
        ],
    )

    def run():
        CONFIGURE.main.__wrapped__()
        return json.loads((config_dir / "opencode.json").read_text(encoding="utf-8"))

    return run


def test_generated_opencode_provider_uses_only_exact_installed_stub_ids(
    generate_config,
):
    config = generate_config()
    local_models = config["provider"]["ollama"]["models"]
    assert set(local_models) >= set(INSTALLED_STUBS)
    assert not (PHANTOM_IDS & set(local_models))
    assert not (PHANTOM_IDS & set(json.dumps(config).split('"')))


def test_missing_stub_uses_exact_direct_cloud_model_when_key_available(
    generate_config, monkeypatch
):
    monkeypatch.setenv("OLLAMA_API_KEY", "dummy-ollama-key")
    monkeypatch.setattr(
        CONFIGURE, "list_cloud_ollama_models", lambda: INSTALLED_STUBS[1:]
    )
    config = generate_config()
    direct = config["provider"]["ollama-cloud"]["models"]
    assert "gemma4:31b" in direct
    assert "gemma4:31b" not in config["provider"]["ollama"]["models"]
    assert "ollama-cloud" not in config["disabled_providers"]


def test_missing_stub_without_direct_key_is_explicitly_unavailable(
    generate_config, monkeypatch, caplog
):
    monkeypatch.setattr(
        CONFIGURE, "list_cloud_ollama_models", lambda: INSTALLED_STUBS[1:]
    )
    config = generate_config()
    assert "ollama-cloud" not in config.get("provider", {})
    assert "ollama-cloud" in config["disabled_providers"]
    assert "OLLAMA_API_KEY is unset" in caplog.text


def test_litellm_canary_never_registers_direct_cloud_even_with_key(
    generate_config, monkeypatch, caplog
):
    monkeypatch.setenv("OLLAMA_API_KEY", "dummy-ollama-key")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("DOTFILES_OPENCODE_USE_LITELLM", "1")
    monkeypatch.setattr(
        CONFIGURE, "list_cloud_ollama_models", lambda: INSTALLED_STUBS[1:]
    )
    config = generate_config()
    assert "ollama-cloud" not in config.get("provider", {})
    assert "ollama-cloud" in config["disabled_providers"]
    assert "direct bypass disabled" in caplog.text
    assert "gemma4:31b" in caplog.text


def test_project_mode_canary_keeps_direct_cloud_provider_disabled(
    generate_config, monkeypatch, caplog
):
    monkeypatch.setenv("OLLAMA_API_KEY", "dummy-ollama-key")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("DOTFILES_OPENCODE_USE_LITELLM", "1")
    monkeypatch.setattr(
        CONFIGURE, "get_preset_providers", lambda *_args: {"ollama-cloud"}
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--preset",
            "pro-plus",
            "--mode",
            "project",
            "--skip",
            "mcps,acp-agents,tier,voice,dcp",
        ],
    )
    config = generate_config()
    assert "ollama-cloud" not in config.get("provider", {})
    assert "ollama-cloud" in config["disabled_providers"]
    assert "Direct ollama-cloud provider omitted" in caplog.text


def test_project_mode_gate_off_keeps_intentional_direct_cloud_provider(
    generate_config, monkeypatch
):
    monkeypatch.setenv("OLLAMA_API_KEY", "dummy-ollama-key")
    monkeypatch.setattr(
        CONFIGURE, "get_preset_providers", lambda *_args: {"ollama-cloud"}
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--preset",
            "pro-plus",
            "--mode",
            "project",
            "--skip",
            "mcps,acp-agents,tier,voice,dcp",
        ],
    )
    config = generate_config()
    assert "ollama-cloud" in config["provider"]


@pytest.mark.parametrize("api_key", ["dummy-ollama-key", ""])
def test_pi_litellm_canary_never_emits_direct_provider_with_cloud_key(
    tmp_path, monkeypatch, caplog, api_key
):
    out = tmp_path / "pi-agent"
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(out))
    if api_key:
        monkeypatch.setenv("OLLAMA_API_KEY", api_key)
    else:
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("DOTFILES_PI_USE_LITELLM", "1")
    monkeypatch.setattr(PI, "load_env", lambda: True)
    monkeypatch.setattr(PI, "list_local_ollama_models", lambda: [])
    monkeypatch.setattr(PI, "check_ollama_daemon", lambda: (True, True))
    monkeypatch.setattr(PI, "list_cloud_ollama_models", lambda: [])
    monkeypatch.setattr(
        PI.tier_registry,
        "materialize_role_models",
        lambda *_args, **_kwargs: {
            "orchestrator": "ollama-cloud/gemma4:31b",
            "librarian": "ollama-cloud/gpt-oss:120b",
        },
    )
    monkeypatch.setattr(PI, "model_entry", lambda model_id, **_kwargs: {"id": model_id})
    monkeypatch.setattr(PI, "_compaction_tokens", lambda _refs: 2048)
    monkeypatch.setattr(
        PI, "get_ollama_local_base_url", lambda: "http://127.0.0.1:11434/v1"
    )
    monkeypatch.setattr(PI, "get_meridian_base_url", lambda: "http://127.0.0.1:3456/v1")
    monkeypatch.setattr(PI, "apply_litellm_provider_overrides", lambda *_args: None)
    monkeypatch.setattr(PI, "_cleanup_generated_agents", lambda *_args: None)
    monkeypatch.setattr(PI, "seed_plugin_configs", lambda **_kwargs: None)
    monkeypatch.setattr(PI, "_ensure_packages", lambda *_args: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [str(PI_SCRIPT), "--preset", "pro-plus", "--no-backup", "--skip", "mcps"],
    )

    PI.main()

    models = json.loads((out / "models.json").read_text(encoding="utf-8"))["providers"]
    settings = json.loads((out / "settings.json").read_text(encoding="utf-8"))
    assert "ollama-cloud" not in models
    assert settings["defaultModel"] == "no-model-available"
    assert "fail closed under the Pi LiteLLM canary" in caplog.text
    assert "gemma4:31b" in caplog.text and "gpt-oss:120b" in caplog.text


def test_models_dev_metadata_lookup_strips_only_final_cloud_suffix(monkeypatch):
    from models_dev import get_ollama_modalities

    metadata = {
        "ollama-cloud": {
            "models": {
                "gemma4:31b": {"modalities": {"input": ["text", "image"]}},
                "glm-5.3": {"modalities": {"input": ["text", "image"]}},
            }
        }
    }
    monkeypatch.setattr(
        "models_dev.get_ollama_show_info",
        lambda _name: {"context_length": None, "capabilities": set()},
    )
    assert get_ollama_modalities("gemma4:31b-cloud", metadata) == {
        "input": ["text", "image"]
    }
    assert get_ollama_modalities("glm-5.3:cloud", metadata) == {
        "input": ["text", "image"]
    }
