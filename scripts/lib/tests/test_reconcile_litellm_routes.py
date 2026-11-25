"""Tests for the read-only subscription-route preflight. See #361."""

import importlib.util
import json
from pathlib import Path

import pytest

import litellm_config
from free_route_manifest import config_binding_digest
import model_catalogues

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "reconcile-litellm-routes.py"


def _load():
    spec = importlib.util.spec_from_file_location("reconcile_routes", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _routes(pairs):
    rows = [
        {"model_name": alias, "litellm_params": {"model": wire}}
        for alias, wire in pairs
    ]
    return {"config_binding_sha256": config_binding_digest(rows), "routes": rows}


@pytest.fixture
def preflight(tmp_path, monkeypatch):
    import sys as _sys

    module = _load()
    projection = tmp_path / "free-route-config.json"
    key_file = tmp_path / "opencode.key"
    key_file.write_text("k-test", encoding="utf-8")
    config = tmp_path / "config.yaml"
    monkeypatch.setattr(module, "PROJECTION_FILE", projection)
    monkeypatch.setattr(module, "KEY_FILE", key_file)
    monkeypatch.setattr(module, "CONFIG_FILE", config)
    monkeypatch.setattr(_sys, "platform", "darwin")
    # Process/YAML evidence is tested independently in test_litellm_generation.
    monkeypatch.setattr(module, "_run_generation_check", lambda *_: None)
    # Pin the report: a real report on the operator host must not decide these.
    monkeypatch.setattr(
        module,
        "chatgpt_verified_openai_models_report",
        lambda environ=None: (
            litellm_config.CHATGPT_REPORT_OK,
            frozenset({"gpt-A", "gpt-B"}),
        ),
    )
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


# --- loaded-generation receipt (361-A) -----------------------------------


@pytest.mark.parametrize("platform_name", ["darwin", "linux"])
def test_missing_receipt_refuses_on_managed_platform(
    preflight, monkeypatch, platform_name
):
    module, projection = preflight
    projection.write_text(json.dumps(_routes([("openai/gpt-A", "chatgpt/gpt-A")])))
    monkeypatch.setattr(module.sys, "platform", platform_name)
    monkeypatch.setattr(
        module, "_run_generation_check", lambda *_: module.REFUSED_NO_RECEIPT
    )

    def _boom(*args, **kwargs):
        pytest.fail("gateway must not be queried without launch evidence")

    monkeypatch.setattr(model_catalogues, "get_models", _boom)
    assert module.main([]) == 1


def test_unmanaged_platform_is_unknown(preflight):
    module, _ = preflight
    assert module._receipt_refusal("4000", "digest", "win32")


def test_projection_digest_refuses_before_gateway(preflight, monkeypatch):
    module, projection = preflight
    document = _routes([("openai/gpt-A", "chatgpt/gpt-A")])
    document["config_binding_sha256"] = "forged"
    projection.write_text(json.dumps(document))
    monkeypatch.setattr(
        model_catalogues, "get_models", lambda *_: pytest.fail("unexpected gateway")
    )
    assert module.main([]) == 1


def test_generation_change_during_advertisement_refuses(preflight, monkeypatch):
    module, projection = preflight
    projection.write_text(json.dumps(_routes([("openai/gpt-A", "chatgpt/gpt-A")])))
    _advertise(monkeypatch, {"openai/gpt-A"})
    reasons = iter([None, module.REFUSED_STALE_GENERATION])
    monkeypatch.setattr(module, "_run_generation_check", lambda *_: next(reasons))
    assert module.main([]) == 1


def test_deep_probe_reports_success(monkeypatch):
    import paseo_http

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        paseo_http, "open_credentialed", lambda request, timeout: _Response()
    )
    module = _load()
    assert module._deep_probe("openai/gpt-A", "http://127.0.0.1:4000/v1", "k") is True


def test_deep_probe_reports_failure(monkeypatch):
    import paseo_http

    def _boom(request, timeout):
        raise OSError("refused")

    monkeypatch.setattr(paseo_http, "open_credentialed", _boom)
    module = _load()
    assert module._deep_probe("openai/gpt-A", "http://127.0.0.1:4000/v1", "k") is False


def test_deep_flag_fails_the_run_when_a_probe_fails(preflight, monkeypatch):
    module, projection = preflight
    projection.write_text(json.dumps(_routes([("openai/gpt-A", "chatgpt/gpt-A")])))
    _advertise(monkeypatch, {"openai/gpt-A"})
    monkeypatch.setattr(module, "_deep_probe", lambda *a, **k: False)
    assert module.main(["--deep"]) == 1
