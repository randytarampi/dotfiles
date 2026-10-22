import importlib.util
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


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
                    ]
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
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
