import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / name)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_voice_omlx_stt_selection_and_opt_out(monkeypatch):
    voice = load_script("configure-opencode-voice.py")
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    monkeypatch.setenv("DOTFILES_USE_LOCAL_OMLX", "true")
    monkeypatch.setenv("OMLX_API_KEY", "secret")
    with patch.object(
        voice, "check_omlx_daemon", return_value=(True, "HTTP 200")
    ), patch.object(
        voice, "check_ollama_daemon", return_value=(False, False)
    ), patch.object(
        voice, "list_local_ollama_models", return_value=[]
    ), patch.object(
        voice.omlx,
        "list_omlx_models",
        return_value=[{"name": "omlx/whisper", "model_type": "audio_stt"}],
    ), patch.object(
        voice.tier_registry,
        "load_registry",
        return_value={"preset": "local", "presets": {}},
    ), patch.object(
        voice.tier_registry, "uses_local_placeholders", return_value=False
    ):
        config = voice.get_voice_config("local")
    assert config["sttEndpoint"].endswith("/v1")
    assert config["sttModel"] == "whisper"
    assert config["sttApiKeyEnv"] == "OMLX_API_KEY"

    monkeypatch.setenv("DOTFILES_USE_LOCAL_OMLX", "false")
    monkeypatch.delenv("OMLX_API_KEY", raising=False)
    with patch.object(
        voice, "check_omlx_daemon", return_value=(True, "HTTP 200")
    ), patch.object(
        voice, "check_ollama_daemon", return_value=(False, False)
    ), patch.object(
        voice, "list_local_ollama_models", return_value=[]
    ), patch.object(
        voice.omlx,
        "list_omlx_models",
        return_value=[{"name": "whisper", "model_type": "audio_stt"}],
    ), patch.object(
        voice.tier_registry, "uses_local_placeholders", return_value=False
    ):
        assert "sttEndpoint" not in voice.get_voice_config("local")


def test_voice_omlx_stt_absent_falls_back(monkeypatch):
    voice = load_script("configure-opencode-voice.py")
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    with patch.object(
        voice, "check_omlx_daemon", return_value=(True, "HTTP 200")
    ), patch.object(voice.omlx, "list_omlx_models", return_value=[]):
        assert "sttEndpoint" not in voice.get_voice_config("local")


def test_caddy_omlx_route_is_gated(monkeypatch):
    caddy = load_script("configure-caddy.py")
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    route = caddy.build_route_block("/tmp/portal")
    assert "/omlx/*" in route
    assert "@omlx_blocked" in route
    assert "127.0.0.1:8000" in route
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "0")
    assert "/omlx/*" not in caddy.build_route_block("/tmp/portal")


def test_codex_omlx_provider_and_profile_are_conditional(monkeypatch):
    codex = load_script("configure-codex.py")
    monkeypatch.setenv("OMLX_BASE_URL", "http://127.0.0.1:8123")
    enabled = codex.build_provider_config(True) + codex.build_profiles_config(
        "cloud", "chat"
    )
    assert "model_providers.omlx" in enabled
    assert "profiles.omlx" in enabled
    disabled = codex.build_provider_config(False) + codex.build_profiles_config("cloud")
    assert "model_providers.omlx" not in disabled
    assert "profiles.omlx" not in disabled


def test_acp_omlx_variants_are_reachable_only(monkeypatch):
    acp = load_script("configure-acp-agents.py")
    monkeypatch.setenv("OMLX_BASE_URL", "http://127.0.0.1:8123")
    with patch.object(acp, "check_omlx_daemon", return_value=(True, "HTTP 200")):
        with patch.object(
            acp.omlx,
            "list_omlx_models",
            return_value=[{"name": "chat", "model_type": "llm"}],
        ):
            agents = acp.build_local_agents("ollama-model", "chat")
    assert agents["claude--omlx"]["env"]["ANTHROPIC_BASE_URL"].endswith("/v1")
    assert "codex--omlx" in agents
    assert not any(
        name.endswith("--omlx") for name in acp.build_local_agents("ollama-model")
    )


def test_acp_main_emits_omlx_agents_and_keeps_legacy_ollama_model(
    tmp_path, monkeypatch
):
    acp = load_script("configure-acp-agents.py")
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    monkeypatch.delenv("OMLX_API_KEY", raising=False)
    output = tmp_path / "acp-agents.json"
    with (
        patch.object(acp, "local_model", return_value="ollama-model"),
        patch.object(acp, "active_pi_tier", return_value="pro"),
        patch.object(acp, "check_omlx_daemon", return_value=(True, "HTTP 200")),
        patch.object(
            acp.omlx,
            "list_omlx_models",
            return_value=[{"name": "omlx-chat", "model_type": "llm"}],
        ),
        patch.object(acp.shutil, "which", return_value="/usr/bin/fake"),
        patch.object(acp, "write_local_junie_config"),
        patch.object(acp, "write_local_codex_config"),
        patch.object(
            sys,
            "argv",
            [
                "configure-acp-agents.py",
                "--output",
                str(output),
                "--agents",
                "claude--omlx,codex--omlx",
            ],
        ),
    ):
        acp.main()
    agents = json.loads(output.read_text())["acpAgents"]
    assert "claude--omlx" in agents
    assert "codex--omlx" in agents
    assert agents["claude--omlx"]["env"]["ANTHROPIC_AUTH_TOKEN"] == "omlx"
