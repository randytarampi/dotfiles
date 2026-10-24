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
    # The installed plugin consumes process.env[cfg.apiKeyEnv] (llm-client.js:85).
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
    monkeypatch.delenv("LITELLM_CLIENT_KEY_FILE", raising=False)
    monkeypatch.setenv("HOME", str(ROOT.parents[2]))
    voice = load_script()
    monkeypatch.setattr(voice, "_configured_litellm_port", lambda: server.server_port)
    yield voice, server, Handler
    server.shutdown()
    thread.join()
    server.server_close()


def test_file_only_key_warns_for_environment_only_consumer(
    tmp_path, monkeypatch, gateway, caplog
):
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
    assert "model" not in config
    assert (
        'export LITELLM_OPENCODE_KEY="$(cat ~/.local/share/litellm/clients/opencode.key)"'
        in caplog.text
    )
    locator = voice._tier_config_only_locator(
        voice.Operation.CLEANUP_LLM, "openai", "gpt-6-luna", "", "API_KEY", True
    )
    assert locator is None


def test_env_only_key_remains_usable(monkeypatch, gateway):
    voice, _server, _handler = gateway
    monkeypatch.setenv("LITELLM_OPENCODE_KEY", "test-key")
    config, locators = {}, {}
    voice._configure_tier_remote(
        config, locators, "openai", "gpt-6-luna", "", "OPENAI_API_KEY", True
    )
    assert config["model"] == "openai/gpt-6-luna"


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
    monkeypatch.setenv("LITELLM_OPENCODE_KEY", "present")
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


def test_config_only_direct_llm_without_stt_reports_config_only(monkeypatch, tmp_path):
    """A gateway-untested direct LLM route is a configuration contract.

    The designed taxonomy labels this state 'config-only' — never 'partial',
    which is reserved for tested gateway LLM delivery without STT. Gates are
    pinned so host exports cannot flip the resolution path.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DOTFILES_USE_LOCAL_OLLAMA", "0")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    voice = load_script()
    config = voice.get_voice_config("plus", {"stt_transport": "openai"})
    assert config.get("_voice_status") == "config-only", (
        f"voice status {config.get('_voice_status')!r}; "
        f"endpoint={config.get('endpoint')!r}; model={config.get('model')!r}; "
        f"apiKeyEnv={config.get('apiKeyEnv')!r}; keys={sorted(config)}; "
        f"selections={config.get('_voice_state', {}).get('selections')!r}"
    )
    assert config.get("endpoint") == "https://api.openai.com/v1"
    assert config.get("model")
    assert config.get("apiKeyEnv") == "OPENAI_API_KEY"


def test_no_resolved_voice_route_is_unresolved(monkeypatch, tmp_path):
    """Gateway mode with no runtime credential omits the route entirely."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DOTFILES_USE_LOCAL_OLLAMA", "0")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    (tmp_path / ".local/share/litellm/clients").mkdir(parents=True)
    monkeypatch.delenv("LITELLM_OPENCODE_KEY", raising=False)
    voice = load_script()
    config = voice.get_voice_config("plus", {"stt_transport": "openai"})
    assert config["_voice_status"] == "unresolved", (
        f"voice status {config.get('_voice_status')!r}; "
        f"endpoint={config.get('endpoint')!r}; model={config.get('model')!r}; "
        f"apiKeyEnv={config.get('apiKeyEnv')!r}; keys={sorted(config)}; "
        f"selections={config.get('_voice_state', {}).get('selections')!r}"
    )
    assert not any(
        key in config for key in ("endpoint", "model", "sttEndpoint", "sttModel")
    )


def test_key_reference_without_resolved_route_is_unresolved(monkeypatch, tmp_path):
    voice = load_script()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DOTFILES_USE_LOCAL_OLLAMA", "0")
    # A non-empty credential reference must exist so the generated entry
    # carries apiKeyEnv while NO route is resolved — pinning that a key
    # reference alone never substitutes for endpoint+model evidence.
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key")
    monkeypatch.setattr(voice, "check_ollama_daemon", lambda: (False, False))
    monkeypatch.setattr(voice, "list_local_ollama_models", lambda: [])
    monkeypatch.setattr(voice.tier_registry, "load_registry", lambda: {"presets": {}})
    monkeypatch.setattr(
        voice.tier_registry, "uses_local_placeholders", lambda *_: False
    )
    monkeypatch.setattr(voice, "get_provider_base_url", lambda *_: None)
    monkeypatch.setattr(
        voice, "resolve_stt_transport", lambda *args, **kwargs: ("openai", None)
    )

    config = voice.get_voice_config("plus")

    # The plugin reads its credential from the environment at runtime
    # (llm-client.js:85); an omitted route leaves NO plugin entry keys — a
    # key reference alone never substitutes for endpoint+model evidence.
    assert config.get("apiKeyEnv") is None
    assert config.get("endpoint") is None
    assert config.get("model") is None
    assert config["_voice_status"] == "unresolved"


def test_main_installs_no_plugin_entry_without_resolved_route(
    monkeypatch, tmp_path, caplog
):
    voice = load_script()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENCODE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.delenv("LITELLM_OPENCODE_KEY", raising=False)
    monkeypatch.setattr(voice, "load_env", lambda *_args: False)
    monkeypatch.setattr(
        voice.sys, "argv", ["configure-opencode-voice.py", "--preset", "local"]
    )
    monkeypatch.setattr(voice, "get_available_tiers", lambda: ["local"])
    monkeypatch.setattr(voice, "check_ollama_daemon", lambda: (False, False))
    monkeypatch.setattr(voice, "audio_models", lambda *_args: [])
    monkeypatch.setattr(
        voice, "_resolve_local_voice_model", lambda *_args: (None, True, False)
    )
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "false")
    monkeypatch.setenv("DOTFILES_USE_LOCAL_OLLAMA", "false")
    monkeypatch.setattr(voice, "is_meridian_configured", lambda: False)

    voice.main()

    tui = json.loads((tmp_path / "tui.json").read_text())
    assert not [
        entry for entry in tui["plugin"] if entry[0] == "@renjfk/opencode-voice"
    ]
    assert "Voice outcome: unresolved; no plugin route installed" in caplog.text


def test_gateway_alias_success_clears_stale_error(monkeypatch, gateway):
    voice, _server, _handler = gateway
    monkeypatch.setattr(voice, "_configured_litellm_port", lambda: _server.server_port)
    monkeypatch.setenv("LITELLM_OPENCODE_KEY", "test-key")
    voice._gateway_alias.last_error = "gateway unauthorized"
    assert voice._gateway_alias("openai", "gpt-6-luna") == "openai/gpt-6-luna"
    assert voice._gateway_alias.last_error is None


def test_gateway_status_classes(monkeypatch, gateway):
    voice, _server, handler = gateway
    monkeypatch.setenv("LITELLM_OPENCODE_KEY", "test-key")
    for status, expected in (
        (401, "gateway unauthorized"),
        (403, "gateway forbidden"),
        (429, "gateway rate limited"),
    ):
        handler.status = status
        assert voice._gateway_alias("openai", "gpt-6-luna") is None
        assert voice._gateway_alias.last_error == expected
        config, locators = {}, {}
        monkeypatch.setenv("LITELLM_OPENCODE_KEY", "test-key")
        voice._configure_tier_remote(
            config, locators, "openai", "gpt-6-luna", "", "OPENAI_API_KEY", True
        )
        assert locators[voice.Operation.CLEANUP_LLM.value]["reason"] == expected
