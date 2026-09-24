import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

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


def test_caddy_omlx_route_is_gated(monkeypatch):
    caddy = load_script("configure-caddy.py")
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    route = caddy.build_route_block("/tmp/portal")
    assert "/omlx/*" in route
    assert "@omlx_blocked" in route
    assert "127.0.0.1:8000" in route
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "0")
    assert "/omlx/*" not in caddy.build_route_block("/tmp/portal")


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
