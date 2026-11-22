"""Tests for subscription-route authorisation. See #361."""

import json
import os

import pytest

import litellm_config
import litellm_route_guard as guard


def _entry(alias, wire):
    return {"model_name": alias, "litellm_params": {"model": wire}}


@pytest.fixture
def oauth_mode(monkeypatch):
    monkeypatch.setattr(guard, "get_litellm_oauth_gate", lambda environ=None: True)
    return monkeypatch


# --- exact wire identity (B1) -------------------------------------------


def test_exact_wire_match_passes():
    ok, reason = guard.planned_subscription_bindings(
        [_entry("openai/gpt-A", "chatgpt/gpt-A")]
    )
    assert (ok, reason) == (True, guard.PLANNED_BINDINGS_VERIFIED)


def test_wrong_model_under_correct_provider_fails():
    """The defect B1 named: same provider prefix, different model."""
    ok, reason = guard.planned_subscription_bindings(
        [_entry("openai/gpt-A", "chatgpt/gpt-B")]
    )
    assert ok is False
    assert reason == f"{guard.REFUSED_WIRE_MISMATCH}:openai/gpt-A"


def test_direct_wire_fails():
    ok, reason = guard.planned_subscription_bindings(
        [_entry("openai/gpt-A", "openai/gpt-A")]
    )
    assert ok is False
    assert reason == f"{guard.REFUSED_WIRE_MISMATCH}:openai/gpt-A"


def test_duplicate_alias_rows_fail():
    ok, reason = guard.planned_subscription_bindings(
        [
            _entry("openai/gpt-A", "chatgpt/gpt-A"),
            _entry("openai/gpt-A", "chatgpt/gpt-B"),
        ]
    )
    assert ok is False
    assert reason == f"{guard.REFUSED_DUPLICATE_ALIAS}:openai/gpt-A"


def test_malformed_row_beside_good_row_fails():
    ok, reason = guard.planned_subscription_bindings(
        [_entry("openai/gpt-A", "chatgpt/gpt-A"), {"model_name": "openai/gpt-B"}]
    )
    assert ok is False
    assert reason.startswith(guard.REFUSED_MALFORMED_ENTRY)


def test_non_openai_aliases_are_not_inspected():
    ok, reason = guard.planned_subscription_bindings(
        [_entry("anthropic/claude", "chatgpt/claude")]
    )
    assert (ok, reason) == (True, guard.NO_SUBSCRIPTION_REQUESTED)


# --- producer-side gate --------------------------------------------------


def test_producer_gate_skips_direct_mode(oauth_mode):
    """A deliberately configured direct route is not this gate's business."""
    ok, reason = guard.assert_subscription_wires(
        {"OPENAI_API_KEY": "sk-direct"}, [_entry("openai/gpt-A", "openai/gpt-A")]
    )
    assert (ok, reason) == (True, guard.NOT_APPLICABLE)


def test_producer_gate_refuses_mistyped_wire(oauth_mode):
    ok, reason = guard.assert_subscription_wires(
        {}, [_entry("openai/gpt-A", "chatgpt/gpt-B")]
    )
    assert ok is False
    assert reason == f"{guard.REFUSED_WIRE_MISMATCH}:openai/gpt-A"


def test_producer_gate_skips_when_oauth_gate_off(monkeypatch):
    monkeypatch.setattr(guard, "get_litellm_oauth_gate", lambda environ=None: False)
    ok, _ = guard.assert_subscription_wires(
        {}, [_entry("openai/gpt-A", "openai/gpt-A")]
    )
    assert ok is True


# --- runtime namespace (B4 + report shrink) ------------------------------


def test_namespace_unknown_service_refuses():
    ok, reason = guard.assert_namespace_matches(["openai/gpt-A"], None)
    assert (ok, reason) == (False, guard.REFUSED_UNKNOWN_SERVICE)


def test_namespace_missing_alias_refuses():
    ok, reason = guard.assert_namespace_matches(["openai/gpt-A"], {"openai/other"})
    assert ok is False
    assert reason == f"{guard.REFUSED_ALIAS_NOT_ADVERTISED}:openai/gpt-A"


def test_namespace_flags_stale_advertised_alias():
    """Report shrank {A,B} -> {A} but the gateway still serves B."""
    ok, reason = guard.assert_namespace_matches(
        ["openai/gpt-A"], {"openai/gpt-A", "openai/gpt-B"}
    )
    assert ok is False
    assert reason == f"{guard.REFUSED_STALE_ALIAS}:openai/gpt-B"


def test_namespace_passes_when_identical():
    ok, reason = guard.assert_namespace_matches(
        ["openai/gpt-A", "openai/gpt-B"],
        {"openai/gpt-A", "openai/gpt-B", "ollama/x"},
    )
    assert (ok, reason) == (True, guard.QUALIFIED)


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
    state, _ = litellm_config.chatgpt_verified_openai_models_report(
        {"HOME": str(tmp_path)}
    )
    assert state == litellm_config.CHATGPT_REPORT_UNSAFE


def test_reader_flags_symlink(tmp_path):
    real = _write_report(tmp_path, ["gpt-6"])
    other = tmp_path / "other"
    link = other / ".local/share/litellm/chatgpt_verified_models.json"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(real)
    state, _ = litellm_config.chatgpt_verified_openai_models_report(
        {"HOME": str(other)}
    )
    assert state == litellm_config.CHATGPT_REPORT_UNSAFE


def test_reader_flags_prefixed_ids_as_malformed(tmp_path):
    _write_report(tmp_path, ["openai/gpt-6"])
    state, _ = litellm_config.chatgpt_verified_openai_models_report(
        {"HOME": str(tmp_path)}
    )
    assert state == litellm_config.CHATGPT_REPORT_MALFORMED


def test_legacy_reader_still_collapses_to_set(tmp_path):
    _write_report(tmp_path, ["gpt-a"])
    assert litellm_config.chatgpt_verified_openai_models({"HOME": str(tmp_path)}) == {
        "gpt-a"
    }
    assert (
        litellm_config.chatgpt_verified_openai_models({"HOME": "/nonexistent"}) == set()
    )
