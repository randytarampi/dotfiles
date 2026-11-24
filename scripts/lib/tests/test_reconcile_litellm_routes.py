"""Tests for the read-only subscription-route preflight. See #361."""

import importlib.util
import json
from pathlib import Path

import pytest

import litellm_config
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
    # Pin the token state: a live cache on the operator's host must not change
    # this test's outcome.
    monkeypatch.setattr(module, "oauth_cache_expiry", lambda provider, environ=None: 1)
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
    _report(monkeypatch, module, litellm_config.CHATGPT_REPORT_OK, ["gpt-A"])
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
    _report(monkeypatch, module, litellm_config.CHATGPT_REPORT_OK, ["gpt-A"])
    _advertise(monkeypatch, {"openai/gpt-A", "ollama/llama"})
    assert module.main([]) == 0


# --- red-first counterexamples from the #389 confirmation review ----------


def _report(monkeypatch, module, state, ids):
    monkeypatch.setattr(
        module,
        "chatgpt_verified_openai_models_report",
        lambda environ=None: (state, frozenset(ids)),
    )


def test_revoked_report_refuses_with_unchanged_projection(preflight, monkeypatch):
    """2.1: report revoked to empty while the projection still plans A."""
    module, projection = preflight
    projection.write_text(json.dumps(_routes([("openai/gpt-A", "chatgpt/gpt-A")])))
    _advertise(monkeypatch, {"openai/gpt-A"})
    _report(monkeypatch, module, litellm_config.CHATGPT_REPORT_OK, [])
    assert module.main([]) == 1


def test_shrunk_report_refuses_partially_revoked_plan(preflight, monkeypatch):
    """2.1: report keeps A but drops B, projection still plans both."""
    module, projection = preflight
    projection.write_text(
        json.dumps(
            _routes(
                [
                    ("openai/gpt-A", "chatgpt/gpt-A"),
                    ("openai/gpt-B", "chatgpt/gpt-B"),
                ]
            )
        )
    )
    _advertise(monkeypatch, {"openai/gpt-A", "openai/gpt-B"})
    _report(monkeypatch, module, litellm_config.CHATGPT_REPORT_OK, ["gpt-A"])
    assert module.main([]) == 1


def test_unusable_report_refuses_when_aliases_are_planned(preflight, monkeypatch):
    module, projection = preflight
    projection.write_text(json.dumps(_routes([("openai/gpt-A", "chatgpt/gpt-A")])))
    _advertise(monkeypatch, {"openai/gpt-A"})
    _report(monkeypatch, module, litellm_config.CHATGPT_REPORT_UNSAFE, [])
    assert module.main([]) == 1


def test_empty_plan_still_detects_stale_advertised_alias(preflight, monkeypatch):
    """2.3: no planned subscription aliases, but a stale one is still served."""
    module, projection = preflight
    projection.write_text(json.dumps(_routes([("ollama/llama", "ollama/llama")])))
    _advertise(monkeypatch, {"ollama/llama", "openai/gpt-A"})
    _report(monkeypatch, module, litellm_config.CHATGPT_REPORT_OK, [])
    assert module.main([]) == 1


def test_empty_plan_and_clean_gateway_passes(preflight, monkeypatch):
    module, projection = preflight
    projection.write_text(json.dumps(_routes([("ollama/llama", "ollama/llama")])))
    _advertise(monkeypatch, {"ollama/llama"})
    _report(monkeypatch, module, litellm_config.CHATGPT_REPORT_OK, [])
    assert module.main([]) == 0


def test_expired_oauth_cache_refuses_planned_aliases(preflight, monkeypatch):
    """Generation would not emit these routes without a live token."""
    module, projection = preflight
    projection.write_text(json.dumps(_routes([("openai/gpt-A", "chatgpt/gpt-A")])))
    monkeypatch.setattr(
        module, "oauth_cache_expiry", lambda provider, environ=None: None
    )
    _report(monkeypatch, module, litellm_config.CHATGPT_REPORT_OK, ["gpt-A"])
    _advertise(monkeypatch, {"openai/gpt-A"})
    assert module.main([]) == 1
