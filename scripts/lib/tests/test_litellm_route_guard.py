"""Tests for subscription-route qualification. See #361."""

import json
import os

import pytest

import litellm_config
import litellm_route_guard as guard


def _env(**overrides):
    environ = {"HOME": "/nonexistent-home"}
    environ.update(overrides)
    return environ


@pytest.fixture
def gate_on(monkeypatch):
    monkeypatch.setattr(guard, "get_litellm_oauth_gate", lambda environ=None: True)
    monkeypatch.setattr(guard, "oauth_cache_expiry", lambda provider, environ=None: 1)
    return monkeypatch


@pytest.fixture
def report(monkeypatch):
    def _set(state, ids):
        monkeypatch.setattr(
            guard,
            "chatgpt_verified_openai_models_report",
            lambda environ=None: (state, frozenset(ids)),
        )

    return _set


@pytest.fixture
def no_inspection(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("service must not be inspected")

    monkeypatch.setattr(guard, "served_routes", _boom)
    return monkeypatch


@pytest.fixture
def routes(monkeypatch):
    def _set(mapping):
        monkeypatch.setattr(guard, "served_routes", lambda *args, **kwargs: mapping)

    return _set


def test_no_openai_refs_never_inspects(no_inspection):
    ok, reason = guard.assert_gateway_routes(
        _env(), ["ollama/llama", "google/gemini-pro"], base_url="x", api_key="k"
    )
    assert (ok, reason) == (True, guard.NO_ROUTES_REQUESTED)


def test_gate_off_never_inspects(no_inspection, monkeypatch):
    monkeypatch.setattr(guard, "get_litellm_oauth_gate", lambda environ=None: False)
    ok, reason = guard.assert_gateway_routes(
        _env(), ["openai/gpt-6"], base_url="x", api_key="k"
    )
    assert (ok, reason) == (True, guard.NO_ROUTES_REQUESTED)


def test_direct_credentials_refuse_without_inspection(gate_on, no_inspection):
    ok, reason = guard.assert_gateway_routes(
        _env(OPENAI_API_KEY="sk-direct"),
        ["openai/gpt-6"],
        base_url="x",
        api_key="k",
    )
    assert (ok, reason) == (False, guard.REFUSED_AMBIGUOUS_CREDENTIALS)


def test_blank_direct_credential_does_not_refuse(gate_on, report, routes):
    report(litellm_config.CHATGPT_REPORT_OK, ["gpt-6"])
    routes({"openai/gpt-6": {"chatgpt"}})
    ok, _ = guard.assert_gateway_routes(
        _env(OPENAI_API_KEY="   "), ["openai/gpt-6"], base_url="x", api_key="k"
    )
    assert ok is True


@pytest.mark.parametrize(
    "state",
    [
        litellm_config.CHATGPT_REPORT_MISSING,
        litellm_config.CHATGPT_REPORT_UNSAFE,
        litellm_config.CHATGPT_REPORT_MALFORMED,
    ],
)
def test_unusable_report_refuses(gate_on, report, no_inspection, state):
    report(state, [])
    ok, reason = guard.assert_gateway_routes(
        _env(), ["openai/gpt-6"], base_url="x", api_key="k"
    )
    assert (ok, reason) == (False, guard.REFUSED_UNUSABLE_REPORT)


def test_ok_empty_report_refuses_as_unqualified(gate_on, report, no_inspection):
    report(litellm_config.CHATGPT_REPORT_OK, [])
    ok, reason = guard.assert_gateway_routes(
        _env(), ["openai/gpt-6"], base_url="x", api_key="k"
    )
    assert (ok, reason) == (False, guard.REFUSED_UNQUALIFIED)


def test_expired_token_means_no_subscription_route_expected(no_inspection, monkeypatch):
    monkeypatch.setattr(guard, "get_litellm_oauth_gate", lambda environ=None: True)
    monkeypatch.setattr(
        guard, "oauth_cache_expiry", lambda provider, environ=None: None
    )
    ok, reason = guard.assert_gateway_routes(
        _env(), ["openai/gpt-6"], base_url="x", api_key="k"
    )
    assert (ok, reason) == (True, guard.NO_ROUTES_REQUESTED)


def test_verified_but_unrequested_id_is_not_required(gate_on, report, routes):
    report(litellm_config.CHATGPT_REPORT_OK, ["gpt-6", "gpt-6-mini"])
    routes({"openai/gpt-6": {"chatgpt"}, "openai/gpt-6-mini": {"chatgpt"}})
    ok, reason = guard.assert_gateway_routes(
        _env(), ["openai/gpt-6"], base_url="x", api_key="k"
    )
    assert (ok, reason) == (True, guard.QUALIFIED)


def test_requested_but_unverified_id_is_not_required(gate_on, report, routes):
    report(litellm_config.CHATGPT_REPORT_OK, ["gpt-6"])
    routes({"openai/gpt-6": {"chatgpt"}})
    ok, _ = guard.assert_gateway_routes(
        _env(), ["openai/gpt-6", "openai/gpt-9"], base_url="x", api_key="k"
    )
    assert ok is True


def test_subscription_binding_qualifies(gate_on, report, routes):
    report(litellm_config.CHATGPT_REPORT_OK, ["gpt-6"])
    routes({"openai/gpt-6": {"chatgpt"}})
    ok, reason = guard.assert_gateway_routes(
        _env(), ["openai/gpt-6"], base_url="x", api_key="k"
    )
    assert (ok, reason) == (True, guard.QUALIFIED)


def test_direct_binding_is_refused(gate_on, report, routes):
    report(litellm_config.CHATGPT_REPORT_OK, ["gpt-6"])
    routes({"openai/gpt-6": {"openai"}})
    ok, reason = guard.assert_gateway_routes(
        _env(), ["openai/gpt-6"], base_url="x", api_key="k"
    )
    assert ok is False
    assert reason == f"{guard.REFUSED_WRONG_BINDING}:openai/gpt-6"


def test_missing_alias_is_refused(gate_on, report, routes):
    report(litellm_config.CHATGPT_REPORT_OK, ["gpt-6"])
    routes({"openai/other": {"chatgpt"}})
    ok, reason = guard.assert_gateway_routes(
        _env(), ["openai/gpt-6"], base_url="x", api_key="k"
    )
    assert ok is False
    assert reason == f"{guard.REFUSED_MISSING_ALIAS}:openai/gpt-6"


def test_mixed_binding_is_refused(gate_on, report, routes):
    report(litellm_config.CHATGPT_REPORT_OK, ["gpt-6"])
    routes({"openai/gpt-6": {"chatgpt", "openai"}})
    ok, reason = guard.assert_gateway_routes(
        _env(), ["openai/gpt-6"], base_url="x", api_key="k"
    )
    assert ok is False
    assert reason == f"{guard.REFUSED_MIXED_BINDING}:openai/gpt-6"


def test_unknown_service_is_refused(gate_on, report, routes):
    report(litellm_config.CHATGPT_REPORT_OK, ["gpt-6"])
    routes(None)
    ok, reason = guard.assert_gateway_routes(
        _env(), ["openai/gpt-6"], base_url="x", api_key="k"
    )
    assert (ok, reason) == (False, guard.REFUSED_UNKNOWN_SERVICE)


def test_served_routes_masks_unknown_shapes():
    assert guard.served_routes("", "k") is None
    assert guard.served_routes("http://127.0.0.1:9/", "") is None


# --- typed report reader -------------------------------------------------


def _write_report(home, payload, mode=0o600):
    path = home / ".local/share/litellm/chatgpt_verified_models.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(path, mode)
    return path


def test_reader_distinguishes_missing_from_ok_empty(tmp_path):
    state, ids = litellm_config.chatgpt_verified_openai_models_report(
        {"HOME": str(tmp_path)}
    )
    assert (state, ids) == (litellm_config.CHATGPT_REPORT_MISSING, frozenset())

    _write_report(tmp_path, [])
    state, ids = litellm_config.chatgpt_verified_openai_models_report(
        {"HOME": str(tmp_path)}
    )
    assert state == litellm_config.CHATGPT_REPORT_OK
    assert ids == frozenset()


def test_reader_flags_insecure_permissions(tmp_path):
    _write_report(tmp_path, ["gpt-6"], mode=0o644)
    state, ids = litellm_config.chatgpt_verified_openai_models_report(
        {"HOME": str(tmp_path)}
    )
    assert (state, ids) == (litellm_config.CHATGPT_REPORT_UNSAFE, frozenset())


def test_reader_flags_symlink(tmp_path):
    real = _write_report(tmp_path, ["gpt-6"])
    other_home = tmp_path / "other"
    link = other_home / ".local/share/litellm/chatgpt_verified_models.json"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(real)
    state, _ = litellm_config.chatgpt_verified_openai_models_report(
        {"HOME": str(other_home)}
    )
    assert state == litellm_config.CHATGPT_REPORT_UNSAFE


def test_reader_flags_prefixed_ids_as_malformed(tmp_path):
    _write_report(tmp_path, ["openai/gpt-6"])
    state, _ = litellm_config.chatgpt_verified_openai_models_report(
        {"HOME": str(tmp_path)}
    )
    assert state == litellm_config.CHATGPT_REPORT_MALFORMED


def test_legacy_reader_still_collapses_to_set(tmp_path):
    _write_report(tmp_path, ["gpt-6"])
    assert litellm_config.chatgpt_verified_openai_models({"HOME": str(tmp_path)}) == {
        "gpt-6"
    }
    assert (
        litellm_config.chatgpt_verified_openai_models({"HOME": "/nonexistent"}) == set()
    )
