import importlib.util
import json
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_script():
    spec = importlib.util.spec_from_file_location(
        "configure_opencode_voice", ROOT / "configure-opencode-voice.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "env_value,file_value,state",
    [
        (None, "file-key", "accepted"),
        ("env-key", None, "accepted"),
        ("same-key", "same-key", "accepted"),
        ("env-key", "file-key", "conflict"),
        (None, None, "absent"),
    ],
)
def test_resolver_matrix(tmp_path, monkeypatch, caplog, env_value, file_value, state):
    from litellm_client_auth import resolve_client_auth

    path = tmp_path / "client.key"
    if file_value:
        path.write_text(file_value)
    if env_value is None:
        monkeypatch.delenv("VOICE_TEST_KEY", raising=False)
    else:
        monkeypatch.setenv("VOICE_TEST_KEY", env_value)
    caplog.set_level(logging.DEBUG)
    result = resolve_client_auth("VOICE_TEST_KEY", path)
    assert result.state == state
    if state == "conflict":
        assert result.detail == "env and file credentials differ"
        captured = result.detail + caplog.text
        assert "env-key" not in captured and "file-key" not in captured
        assert "".join(str(ord(c)) for c in "env-key") not in captured
    if state == "absent":
        assert not result.authorized


@pytest.fixture
def gateway(monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        status = 200

        def do_GET(self):
            self.send_response(self.status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if self.status == 200:
                self.wfile.write(
                    json.dumps({"data": [{"id": "openai/gpt-6-luna"}]}).encode()
                )

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    voice = load_script()
    monkeypatch.setattr(voice, "_configured_litellm_port", lambda: server.server_port)
    yield voice, server, Handler
    server.shutdown()
    thread.join()
    server.server_close()


def test_file_only_key_satisfies_gateway_gate(tmp_path, monkeypatch, gateway):
    voice, _server, _handler = gateway
    key_file = tmp_path / "LITELLM_OPENCODE_KEY"
    key_file.write_text("file-only-secret")
    monkeypatch.setenv("HOME", str(tmp_path))
    clients = tmp_path / ".local/share/litellm/clients"
    clients.mkdir(parents=True)
    (clients / "opencode.key").write_text("file-only-secret")
    monkeypatch.delenv("LITELLM_OPENCODE_KEY", raising=False)
    config, locators = {}, {}
    voice._configure_tier_remote(
        config, locators, "openai", "gpt-6-luna", "", "OPENAI_API_KEY", True
    )
    assert config["model"] == "openai/gpt-6-luna"
    locator = voice._tier_config_only_locator(
        voice.Operation.CLEANUP_LLM, "openai", "gpt-6-luna", "", "API_KEY", True
    )
    assert locator is not None


def test_absent_key_and_401_are_reported_truthfully(
    tmp_path, monkeypatch, gateway, caplog
):
    voice, _server, handler = gateway
    monkeypatch.delenv("LITELLM_OPENCODE_KEY", raising=False)
    monkeypatch.setenv("LITELLM_CLIENT_KEY_FILE", str(tmp_path / "missing"))
    _, absent = {}, {}
    voice._configure_tier_remote(
        _, absent, "openai", "gpt-6-luna", "", "OPENAI_API_KEY", True
    )
    assert "key" in absent[voice.Operation.CLEANUP_LLM.value]["reason"]
    assert "cleanup_llm" in caplog.text and "gpt-6-luna" in caplog.text

    key_file = tmp_path / "key"
    key_file.write_text("present")
    clients = tmp_path / ".local/share/litellm/clients"
    clients.mkdir(parents=True, exist_ok=True)
    (clients / "opencode.key").write_text("present")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("LITELLM_CLIENT_KEY_FILE", raising=False)
    handler.status = 401
    voice._configure_tier_remote(
        {}, {}, "openai", "gpt-6-luna", "", "OPENAI_API_KEY", True
    )
    assert "unauthorized" in caplog.text.lower()


def test_omission_messages_label_operation_model(tmp_path, monkeypatch, caplog):
    voice = load_script()
    home = tmp_path / "home"
    (home / ".local/share/litellm/clients").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("LITELLM_OPENCODE_KEY", raising=False)
    caplog.set_level(logging.WARNING)
    locator = voice._tier_config_only_locator(
        voice.Operation.CLEANUP_LLM, "openai", "gpt-6-luna", "", "KEY", True
    )
    assert locator is None
    text = caplog.text
    assert "cleanup_llm" in text and "gpt-6-luna" in text
    assert "omitting" in text.lower()


def test_incomplete_cleanup_is_partial_status():
    voice = load_script()
    config = voice.get_voice_config("plus", {"stt_transport": "openai"})
    assert config.get("_voice_status") == "partial"
