import importlib.util
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "lib"

sys.path.insert(0, str(LIB))
from opencode_config import (  # noqa: E402 -- scripts/lib bootstrap ordering.
    get_available_tiers,
)  # noqa: E402 -- scripts/lib bootstrap ordering.


def load_script():
    spec = importlib.util.spec_from_file_location(
        "configure_opencode_voice", ROOT / "configure-opencode-voice.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def served_inventory(monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(
                {
                    "data": [
                        {"id": "ollama/llama3.2:3b"},
                        {"id": "opencode/deepseek-ai/model-x"},
                        {"id": "opencode/muse-spark-1.3-contributor-free"},
                        {"id": "opencode/big-pickle"},
                    ]
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            # Intentionally silent: the fixture server must not spam stderr with per-request logs.
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("LITELLM_OPENCODE_KEY", "test-key")
    yield server.server_port
    server.shutdown()
    thread.join()
    server.server_close()


@pytest.mark.parametrize(
    "model, expected",
    [
        ("ollama/llama3.2:3b", "ollama/llama3.2:3b"),
        ("llama3.2:3b", "ollama/llama3.2:3b"),
        ("deepseek-ai/model-x", "opencode/deepseek-ai/model-x"),
    ],
)
def test_both_tier_locators_normalize_once_against_real_gateway(
    monkeypatch, served_inventory, model, expected
):
    voice = load_script()
    monkeypatch.setattr(voice, "_configured_litellm_port", lambda: served_inventory)
    assert voice._gateway_alias("ollama", "llama3.2:3b") == "ollama/llama3.2:3b"

    # Exercise an upstream namespace containing an interior slash without
    # stripping it as though it were an additional provider prefix.
    provider = "opencode" if model.startswith("deepseek-ai/") else "ollama"
    voice_config, locators = {}, {}
    voice._configure_tier_remote(
        voice_config,
        locators,
        provider,
        model,
        "https://direct.example/v1",
        "API_KEY",
        True,
    )
    assert voice_config["model"] == expected

    locator = voice._tier_config_only_locator(
        voice.Operation.CLEANUP_LLM,
        provider,
        model,
        "https://direct.example/v1",
        "API_KEY",
        True,
    )
    assert locator.route.wire_model == expected


def test_free_tier_consumes_real_gateway_alias_and_omits_on_unauthorized(
    monkeypatch, served_inventory
):
    voice = load_script()
    monkeypatch.setattr(voice, "_configured_litellm_port", lambda: served_inventory)
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("LITELLM_OPENCODE_KEY", "test-key")
    monkeypatch.setattr(voice, "check_ollama_daemon", lambda: (False, False))
    assert "free" in get_available_tiers()
    config = voice.get_voice_config("free")
    expected = "opencode/big-pickle"
    assert config["model"] == expected
    assert config["model"].count("opencode/") == 1
    assert config["model"] != "opencode/opencode/muse-spark-1.3-contributor-free"

    class Unauthorized(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_error(401)

        def log_message(self, format, *args):
            # Intentionally silent: keep the unauthorized-response test output clean.
            pass

    server = HTTPServer(("127.0.0.1", 0), Unauthorized)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setattr(voice, "check_ollama_daemon", lambda: (False, False))
        monkeypatch.setattr(
            voice, "_configured_litellm_port", lambda: server.server_port
        )
        # Distinct 401 status reasons arrive with #75 (effd6ba); this branch
        # asserts fail-closed omission and retains the attempted-route reason.
        omitted = voice.get_voice_config("free")
        assert "model" not in omitted
        assert "endpoint" not in omitted
        recorded = omitted["_voice_state"]["selections"][
            voice.Operation.CLEANUP_LLM.value
        ]
        assert recorded["transport"] == "unavailable"
        assert recorded["reason"]
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_free_tier_direct_preserves_interior_slash_and_strict_gateway_has_no_direct_route(
    monkeypatch, served_inventory
):
    voice = load_script()
    monkeypatch.setattr(voice, "check_ollama_daemon", lambda: (False, False))
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    monkeypatch.delenv("LITELLM_OPENCODE_KEY", raising=False)
    direct = voice.get_voice_config("free")
    assert direct["model"] == "big-pickle"
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    strict = voice.get_voice_config("free")
    assert "endpoint" not in strict
