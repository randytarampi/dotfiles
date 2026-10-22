from __future__ import annotations

import pytest
import route_probes

import constants
import openwebui


@pytest.mark.parametrize("http_code", [500])
def test_embedding_http_failure_reason_includes_status_and_is_sanitized(
    monkeypatch, http_code, caplog
):
    monkeypatch.setattr(constants, "get_litellm_proxy_mode", lambda: True)
    monkeypatch.setattr(openwebui, "_gateway_embedding_alias", lambda: "ollama/embed")
    monkeypatch.setattr(openwebui, "_litellm_endpoint", lambda: "http://gateway/v1")
    monkeypatch.setattr(openwebui, "_litellm_key_value", lambda: "private-value")
    monkeypatch.setattr(openwebui, "_read_embedding_state", lambda: {})
    monkeypatch.setattr(openwebui, "_read_audio_selection", lambda: None)
    monkeypatch.setattr(openwebui, "_route_engine_build", lambda *args: "build")
    monkeypatch.setattr(
        route_probes,
        "probe_embeddings",
        lambda *a, **k: route_probes.ProbeResult(
            "unavailable",
            "ollama/embed",
            "ollama/embed",
            "http://gateway/v1/embeddings",
            ("HTTP request rejected",),
            http_status=http_code,
        ),
    )
    records = {}

    assert (
        openwebui._desired_embedding_settings(
            probe_routes=True, selection_records=records
        )
        is None
    )
    reason = records["_unavailable"]["reason"]
    assert "500" in reason
    assert "connection unavailable or timed out" not in reason
    assert "private-value" not in reason
    assert "LITELLM_OPENWEBUI_KEY_PATH" not in reason
    assert "gateway returned HTTP 500" in caplog.text


def test_embedding_connection_failure_keeps_connection_reason(monkeypatch):
    monkeypatch.setattr(constants, "get_litellm_proxy_mode", lambda: True)
    monkeypatch.setattr(openwebui, "_gateway_embedding_alias", lambda: "ollama/embed")
    monkeypatch.setattr(openwebui, "_litellm_endpoint", lambda: "http://gateway/v1")
    monkeypatch.setattr(openwebui, "_litellm_key_value", lambda: None)
    monkeypatch.setattr(openwebui, "_read_embedding_state", lambda: {})
    monkeypatch.setattr(openwebui, "_read_audio_selection", lambda: None)
    monkeypatch.setattr(openwebui, "_route_engine_build", lambda *args: "build")
    monkeypatch.setattr(
        route_probes,
        "probe_embeddings",
        lambda *a, **k: route_probes.ProbeResult(
            "unknown",
            "ollama/embed",
            "ollama/embed",
            "http://gateway/v1/embeddings",
            ("connection unavailable or timed out",),
        ),
    )
    records = {}

    assert (
        openwebui._desired_embedding_settings(
            probe_routes=True, selection_records=records
        )
        is None
    )
    assert records["_unavailable"]["reason"] == "connection unavailable or timed out"
