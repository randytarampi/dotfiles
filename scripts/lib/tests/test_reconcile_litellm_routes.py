"""Tests for the read-only subscription-route preflight. See #361."""

import importlib.util
import json
from pathlib import Path

import pytest

import model_catalogues

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "reconcile-litellm-routes.py"


def _load():
    spec = importlib.util.spec_from_file_location("reconcile_routes", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _routes(pairs):
    return {
        "config_binding_sha256": "deadbeef",
        "routes": [
            {"model_name": alias, "litellm_params": {"model": wire}}
            for alias, wire in pairs
        ],
    }


@pytest.fixture
def preflight(tmp_path, monkeypatch):
    module = _load()
    projection = tmp_path / "free-route-config.json"
    key_file = tmp_path / "opencode.key"
    key_file.write_text("k-test", encoding="utf-8")
    monkeypatch.setattr(module, "PROJECTION_FILE", projection)
    monkeypatch.setattr(module, "KEY_FILE", key_file)
    monkeypatch.setattr(module, "get_litellm_oauth_gate", lambda environ=None: True)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return module, projection


def _advertise(monkeypatch, served):
    monkeypatch.setattr(model_catalogues, "get_models", lambda url, key: served)


def test_missing_projection_fails_closed(preflight):
    module, _ = preflight
    assert module.main([]) == 1


def test_projection_without_subscription_routes_passes(preflight, monkeypatch):
    module, projection = preflight
    projection.write_text(json.dumps(_routes([("ollama/llama", "ollama/llama")])))
    _advertise(monkeypatch, set())
    assert module.main([]) == 0


def test_gate_off_permits_a_direct_route(preflight, monkeypatch):
    module, projection = preflight
    projection.write_text(json.dumps(_routes([("openai/gpt-A", "openai/gpt-A")])))
    monkeypatch.setattr(module, "get_litellm_oauth_gate", lambda environ=None: False)
    _advertise(monkeypatch, set())
    assert module.main([]) == 0


def test_direct_paid_key_under_oauth_refuses_before_any_probe(preflight, monkeypatch):
    module, projection = preflight
    projection.write_text(json.dumps(_routes([("openai/gpt-A", "chatgpt/gpt-A")])))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-direct")

    def _boom(*args, **kwargs):
        raise AssertionError("must not probe the gateway when refusing credentials")

    monkeypatch.setattr(model_catalogues, "get_models", _boom)
    assert module.main([]) == 1


def test_wire_mismatch_refuses(preflight, monkeypatch):
    module, projection = preflight
    projection.write_text(json.dumps(_routes([("openai/gpt-A", "chatgpt/gpt-B")])))
    _advertise(monkeypatch, {"openai/gpt-A"})
    assert module.main([]) == 1


def test_unadvertised_alias_refuses(preflight, monkeypatch):
    module, projection = preflight
    projection.write_text(json.dumps(_routes([("openai/gpt-A", "chatgpt/gpt-A")])))
    _advertise(monkeypatch, {"openai/other"})
    assert module.main([]) == 1


def test_stale_advertised_alias_refuses(preflight, monkeypatch):
    module, projection = preflight
    projection.write_text(json.dumps(_routes([("openai/gpt-A", "chatgpt/gpt-A")])))
    _advertise(monkeypatch, {"openai/gpt-A", "openai/gpt-B"})
    assert module.main([]) == 1


def test_unavailable_catalogue_is_unknown_not_success(preflight, monkeypatch):
    module, projection = preflight
    projection.write_text(json.dumps(_routes([("openai/gpt-A", "chatgpt/gpt-A")])))
    _advertise(monkeypatch, None)
    assert module.main([]) == 1


def test_qualified_passes(preflight, monkeypatch):
    module, projection = preflight
    projection.write_text(
        json.dumps(
            _routes(
                [
                    ("openai/gpt-A", "chatgpt/gpt-A"),
                    ("ollama/llama", "ollama/llama"),
                ]
            )
        )
    )
    _advertise(monkeypatch, {"openai/gpt-A", "ollama/llama"})
    assert module.main([]) == 0
