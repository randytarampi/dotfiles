from __future__ import annotations

import importlib.util
import logging
import urllib.error
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "configure-openwebui.py"
SPEC = importlib.util.spec_from_file_location("configure_openwebui", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
configure_openwebui = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(configure_openwebui)


@pytest.mark.parametrize(
    "provider,key_env,credential,outcome,expected",
    [
        ("openai", "OPENAI_API_KEY", "", "skip", "not configured"),
        ("openai", "OPENAI_API_KEY", "secret-test-value", "empty", "unavailable live"),
        ("openai", "OPENAI_API_KEY", "secret-test-value", "401", "HTTP 401"),
        ("openai", "OPENAI_API_KEY", "secret-test-value", "500", "HTTP 500"),
        ("openai", "OPENAI_API_KEY", "secret-test-value", "url", "connection error"),
    ],
)
def test_curated_catalogue_messages(
    monkeypatch, caplog, provider, key_env, credential, outcome, expected
):
    if credential:
        monkeypatch.setenv(key_env, credential)
    else:
        monkeypatch.delenv(key_env, raising=False)
    for env_name in (
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENCODE_API_KEY",
    ):
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setattr(
        configure_openwebui,
        "PROVIDER_ENDPOINTS",
        {
            "google": {"apiKeyEnv": "GEMINI_API_KEY"},
            "openrouter": {"apiKeyEnv": "OPENROUTER_API_KEY"},
            "opencode": {"apiKeyEnv": "OPENCODE_API_KEY"},
        },
    )
    monkeypatch.setattr(configure_openwebui, "provider_models", lambda _provider: [])
    if outcome == "skip":
        with caplog.at_level(logging.INFO):
            assert configure_openwebui._curated_cloud_models() == []
    else:

        class Client:
            def get_models_config(self):
                return {}

            def get_models(self):
                if outcome == "empty":
                    return {"data": []}
                if outcome == "401":
                    error = configure_openwebui.OpenWebUIError("lookup failed")
                    error.status = 401
                    raise error
                if outcome == "500":
                    error = configure_openwebui.OpenWebUIError("lookup failed")
                    error.status = 500
                    raise error
                error = configure_openwebui.OpenWebUIError("lookup failed")
                error.__cause__ = urllib.error.URLError("offline")
                raise error

        monkeypatch.setattr(
            configure_openwebui, "_curated_cloud_models", lambda: ["openai/model"]
        )
        monkeypatch.setattr(
            configure_openwebui,
            "_catalogue_state_path",
            lambda **_kwargs: type("StatePath", (), {"is_file": lambda _self: False})(),
        )
        monkeypatch.setattr(
            configure_openwebui,
            "_load_catalogue_state",
            lambda *_args, **_kwargs: (set(), set()),
        )
        monkeypatch.setattr(
            configure_openwebui, "_write_catalogue_state", lambda *_args: None
        )
        with caplog.at_level(logging.INFO):
            configure_openwebui._reconcile_catalogue(Client(), dry_run=True)

    messages = " ".join(record.getMessage() for record in caplog.records)
    assert provider in messages
    assert expected in messages
    if credential:
        assert credential not in messages
