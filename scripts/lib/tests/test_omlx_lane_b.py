import importlib.util
import argparse
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from constants import OMLX_DEFAULT_PORT

import local_engines

ROOT = Path(__file__).resolve().parents[2]

# Ambient OMLX_*/gate vars from a developer's sourced ~/.env leak into tests
# that assume an env-free ambient (default ports, no live daemon, gates off).
# Strip them so results are hermetic regardless of the invoking shell; tests
# that need them set them explicitly via monkeypatch.setenv.
HERMETIC_ENV_VARS = (
    "OMLX_BASE_URL",
    "OMLX_HOST",
    "OMLX_PORT",
    "OMLX_API_KEY",
    "OMLX_MCP_EXPOSE_TOOLS",
    "DOTFILES_RUN_OMLX_SETUP",
    "DOTFILES_USE_LOCAL_OMLX",
)


@pytest.fixture(autouse=True)
def _isolate_omlx_env(monkeypatch):
    for name in HERMETIC_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / name)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_voice_local_stt_uses_registered_engine_and_opt_out(monkeypatch):
    voice = load_script("configure-opencode-voice.py")
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    monkeypatch.setenv("DOTFILES_USE_LOCAL_OMLX", "true")
    monkeypatch.setenv("OMLX_API_KEY", "secret")
    # Voice-LLM resolution is fail-closed (PR1) and is not this test's
    # subject. Stub its inputs so the STT assertions stay hermetic and do
    # not depend on a live Ollama daemon or on tier-registry contents.
    monkeypatch.setattr(
        voice,
        "list_local_ollama_models",
        lambda: [{"name": "smol-coder:4b", "provider": "ollama"}],
    )
    monkeypatch.setattr(
        voice,
        "resolve_roles_from_list",
        lambda *args, **kwargs: {"lightweight": "ollama/smol-coder:4b"},
    )
    monkeypatch.setattr(
        voice,
        "get_model_details",
        lambda name, provider="ollama": {
            "param_count": 4,
            "capabilities": ["completion", "tools"],
            "is_moe": False,
            "model_type": "llm",
        },
    )
    with (
        patch.dict(
            local_engines.LOCAL_ENGINES["omlx"],
            {
                "health_check": lambda: (True, "HTTP 200"),
                "audio_discovery": lambda: [
                    {"name": "omlx/whisper", "model_type": "audio_stt"}
                ],
                "list_models": lambda include_cloud=False: [],
                "base_url": lambda: "http://127.0.0.1:8000",
            },
        ),
        patch.object(
            voice.tier_registry,
            "load_registry",
            return_value={
                "preset": "local",
                "presets": {
                    "local": {
                        "librarian": {"model": "_local:lightweight"},
                    }
                },
            },
        ),
        patch.object(
            voice.tier_registry, "uses_local_placeholders", return_value=False
        ),
    ):
        config = voice.get_voice_config("local")
    assert config["sttEndpoint"].endswith("/v1")
    assert config["sttModel"] == "whisper"
    assert config["sttApiKeyEnv"] == "OMLX_API_KEY"

    monkeypatch.setenv("DOTFILES_USE_LOCAL_OMLX", "false")
    monkeypatch.delenv("OMLX_API_KEY", raising=False)
    with patch.object(
        voice.tier_registry, "uses_local_placeholders", return_value=False
    ):
        assert "sttEndpoint" not in voice.get_voice_config("local")


def test_voice_local_stt_absent_falls_back(monkeypatch):
    voice = load_script("configure-opencode-voice.py")
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    with patch.dict(
        local_engines.LOCAL_ENGINES["omlx"],
        {
            "audio_discovery": lambda: [],
            "list_models": lambda include_cloud=False: [],
        },
    ):
        assert "sttEndpoint" not in voice.get_voice_config("local")


def test_voice_empty_pool_is_unresolved_without_partial_config(monkeypatch, caplog):
    voice = load_script("configure-opencode-voice.py")
    monkeypatch.setattr(
        voice,
        "iter_engine_discovery",
        lambda provider: local_engines.DiscoveryResult("ok_empty", ()),
    )
    monkeypatch.setattr(voice, "check_ollama_daemon", lambda: (False, False))
    with (
        patch.object(
            voice.tier_registry, "load_registry", return_value={"presets": {}}
        ),
        patch.object(
            voice.tier_registry, "uses_local_placeholders", return_value=False
        ),
        patch.object(voice.tier_registry, "materialize_role_models", return_value={}),
        caplog.at_level("WARNING"),
    ):
        result = voice.get_voice_config("local")
    assert result == {"_voice_unresolved": True}
    assert "unresolved" in caplog.text


@pytest.mark.parametrize("status", ["unknown_error", "ok_models"])
def test_voice_unverified_or_unknown_pool_is_unresolved(monkeypatch, status):
    voice = load_script("configure-opencode-voice.py")
    records = (
        ({"name": "omlx/audio", "provider": "omlx"},) if status == "ok_models" else ()
    )
    monkeypatch.setattr(
        voice,
        "iter_engine_discovery",
        lambda provider: local_engines.DiscoveryResult(status, records),
    )
    monkeypatch.setattr(voice, "check_ollama_daemon", lambda: (False, False))
    monkeypatch.setattr(
        voice, "get_model_details", lambda *a, **k: {"capabilities": ["audio"]}
    )
    with (
        patch.object(
            voice.tier_registry, "load_registry", return_value={"presets": {}}
        ),
        patch.object(
            voice.tier_registry, "uses_local_placeholders", return_value=False
        ),
        patch.object(voice.tier_registry, "materialize_role_models", return_value={}),
    ):
        assert voice.get_voice_config("local")["_voice_unresolved"] is True


def test_voice_dry_run_never_prints_foreign_plugin_values(
    monkeypatch, tmp_path, capsys
):
    voice = load_script("configure-opencode-voice.py")
    sentinel = "VOICE_SENTINEL_SECRET_92814"
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "tui.json").write_text(
        json.dumps(
            {
                "plugin": [["foreign", {"nested": {"token": sentinel}}]],
                "setting": sentinel,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCODE_DIR", str(config_dir))
    monkeypatch.setattr(sys, "argv", ["voice", "--preset", "pro-plus", "--dry-run"])
    monkeypatch.setattr(
        voice,
        "get_voice_config",
        lambda *a: {"endpoint": "http://example", "model": "model"},
    )
    voice.main()
    assert sentinel not in capsys.readouterr().out


def _voice_test_config(monkeypatch, voice, discovery, key=None):
    monkeypatch.setattr(voice, "check_ollama_daemon", lambda: (False, False))
    monkeypatch.setattr(voice, "list_local_ollama_models", lambda: [])
    monkeypatch.setattr(
        local_engines, "engine_gate_active", lambda provider: provider == "omlx"
    )
    if key is None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENAI_API_KEY", key)
    with (
        patch.dict(
            local_engines.LOCAL_ENGINES["omlx"], {"audio_discovery": lambda: discovery}
        ),
        patch.object(
            voice.tier_registry, "load_registry", return_value={"presets": {}}
        ),
        patch.object(
            voice.tier_registry, "uses_local_placeholders", return_value=False
        ),
    ):
        return voice.get_voice_config("pro-plus")


def test_voice_auto_discovered_omlx_emits_no_tts_keys(monkeypatch):
    voice = load_script("configure-opencode-voice.py")
    monkeypatch.setenv("DOTFILES_VOICE_TTS_VOICE", "vivian")
    monkeypatch.setenv("OMLX_PORT", "11427")
    config = _voice_test_config(
        monkeypatch, voice, [{"name": "omlx/model-a", "model_type": "audio_tts"}]
    )
    assert not any(key.lower().startswith("tts") for key in config)


def test_voice_auto_without_local_tts_logs_consumer_note(monkeypatch, caplog):
    voice = load_script("configure-opencode-voice.py")
    with caplog.at_level("WARNING"):
        config = _voice_test_config(monkeypatch, voice, [])
    assert not any(key.lower().startswith("tts") for key in config)
    assert "synthesizes via local Piper" in caplog.text
    assert "Open WebUI audio bundle" in caplog.text


def test_voice_explicit_piper(monkeypatch):
    voice = load_script("configure-opencode-voice.py")
    monkeypatch.setattr(voice, "check_ollama_daemon", lambda: (False, False))
    monkeypatch.setattr(voice, "list_local_ollama_models", lambda: [])
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with (
        patch.object(
            voice.tier_registry, "load_registry", return_value={"presets": {}}
        ),
        patch.object(
            voice.tier_registry, "uses_local_placeholders", return_value=False
        ),
    ):
        config = voice.get_voice_config("pro-plus")
    assert not any(key.lower().startswith("tts") for key in config)


def test_voice_openai_credentials_do_not_emit_tts_config(monkeypatch):
    voice = load_script("configure-opencode-voice.py")
    monkeypatch.setattr(voice, "check_ollama_daemon", lambda: (False, False))
    monkeypatch.setattr(voice, "list_local_ollama_models", lambda: [])
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    with (
        patch.object(
            voice.tier_registry, "load_registry", return_value={"presets": {}}
        ),
        patch.object(
            voice.tier_registry, "uses_local_placeholders", return_value=False
        ),
    ):
        config = voice.get_voice_config("pro-plus")
    assert not any(key.lower().startswith("tts") for key in config)


def test_voice_parser_rejects_removed_tts_transport(monkeypatch):
    voice = load_script("configure-opencode-voice.py")
    monkeypatch.setattr(voice, "get_available_tiers", lambda: ["pro-plus"])
    monkeypatch.setattr(voice, "add_model_override_args", lambda parser: None)
    monkeypatch.setattr(voice, "add_min_reasoning_embedding_arg", lambda parser: None)
    monkeypatch.setattr(voice, "load_env", lambda *args: True)
    monkeypatch.setattr(voice.os.path, "exists", lambda path: False)
    monkeypatch.setattr(voice.os.path, "expanduser", lambda path: "/tmp/not-real-home")
    monkeypatch.setattr(voice.os, "makedirs", lambda *args, **kwargs: None)
    monkeypatch.setattr(voice, "write_text_file", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        sys, "argv", ["configure-opencode-voice.py", "--tts-transport", "auto"]
    )
    with pytest.raises(SystemExit) as error:
        voice.main()
    assert error.value.code == 2


def test_caddy_omlx_route_is_gated(monkeypatch):
    caddy = load_script("configure-caddy.py")
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    route = caddy.build_route_block("/tmp/portal")
    assert "/omlx/*" in route
    assert "@omlx_blocked" in route
    assert f"127.0.0.1:{OMLX_DEFAULT_PORT}" in route
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "0")
    assert "/omlx/*" not in caddy.build_route_block("/tmp/portal")


def test_caddy_auth_matcher_is_defined_in_each_site_block(monkeypatch):
    caddy = load_script("configure-caddy.py")
    monkeypatch.setenv("OMLX_API_KEY", "secret")
    auth = caddy.build_auth_block([("user", "hashed-password")]).replace(
        "basic_auth {", "basic_auth @not_omlx {", 1
    )
    blocks = [
        caddy.build_site_block(
            "https://example.com", "0.0.0.0", "tls internal", auth, ""
        ),
        caddy.build_opencode_site_block(
            "https://opencode.example.com", "0.0.0.0", "tls internal", auth, "4096"
        ),
    ]

    assert len(blocks) == 2
    for block in blocks:
        lines = block.splitlines()
        auth_index = next(
            index
            for index, line in enumerate(lines)
            if "basic_auth @not_omlx {" in line
        )
        assert "  @not_omlx not path /omlx/*" in lines[:auth_index]


def test_codex_local_profile_follows_pool_winner_engine(monkeypatch):
    codex = load_script("configure-codex.py")
    monkeypatch.setenv("OMLX_BASE_URL", "http://127.0.0.1:8123")
    monkeypatch.setattr(local_engines, "engine_gate_active", lambda p: p == "omlx")
    enabled = codex.build_provider_config("omlx/chat") + codex.build_profiles_config(
        "cloud", "omlx/chat"
    )
    assert "model_providers.omlx" in enabled
    assert "profiles.local" in enabled
    assert 'model = "chat"' in enabled
    monkeypatch.setattr(local_engines, "engine_gate_active", lambda p: False)
    ollama = codex.build_provider_config("ollama/chat") + codex.build_profiles_config(
        "cloud", "ollama/chat"
    )
    assert "model_providers.omlx" not in ollama
    assert 'model_provider = "ollama"' in ollama


def test_acp_local_agent_follows_pool_winner_engine(monkeypatch):
    acp = load_script("configure-acp-agents.py")
    monkeypatch.setenv("OMLX_BASE_URL", "http://127.0.0.1:8123")
    monkeypatch.setattr(local_engines, "engine_gate_active", lambda p: True)
    agents = acp.build_local_agents("omlx/chat")
    assert agents["claude--local"]["env"]["ANTHROPIC_BASE_URL"].endswith("/v1")
    assert agents["codex--local"]["args"][:2] == ["--profile", "local"]
    assert not any(name.endswith("--omlx") for name in agents)
    # Ollama cannot serve Anthropic (/v1/messages): claude--local omits
    # itself instead of pointing at a broken endpoint; the rest survive.
    ollama_agents = acp.build_local_agents("ollama-model")
    assert "claude--local" not in ollama_agents
    assert "codex--local" in ollama_agents
    assert "pi--local" in ollama_agents


def test_acp_main_emits_pool_driven_local_agents(tmp_path, monkeypatch):
    acp = load_script("configure-acp-agents.py")
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    monkeypatch.delenv("OMLX_API_KEY", raising=False)
    output = tmp_path / "acp-agents.json"
    output.write_text(json.dumps({"acpAgents": {"claude--omlx": {}}}))
    with (
        patch.object(acp, "local_model", return_value="omlx/omlx-chat"),
        patch.object(acp, "active_pi_tier", return_value="pro"),
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
                "claude--local,codex--local",
            ],
        ),
    ):
        acp.main()
    agents = json.loads(output.read_text())["acpAgents"]
    assert "claude--local" in agents
    assert "codex--local" in agents
    assert not any(name.endswith("--omlx") for name in agents)
