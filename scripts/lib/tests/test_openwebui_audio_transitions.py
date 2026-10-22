import json
import logging

import pytest

import openwebui


class AudioClient:
    def __init__(self, audio):
        self.audio = audio

    def get_audio_config(self):
        return self.audio

    def update_audio_config(self, audio):
        self.audio = audio


def test_unchanged_audio_credential_transition_does_not_rewrite_marker(
    monkeypatch, tmp_path, caplog
):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    monkeypatch.setenv("AUDIO_TEST_KEY", "managed-key")
    client = AudioClient({"stt": {"OPENAI_API_KEY": ""}})
    desired = {"stt": {"MODEL": "asr", "OPENAI_API_KEY": "managed-key"}}

    openwebui._reconcile_audio(client, client.audio, desired)
    marker = tmp_path / "managed-audio.json.credentials"
    before = marker.read_bytes()
    caplog.clear()

    openwebui._reconcile_audio(client, client.audio, desired)

    assert marker.read_bytes() == before
    assert not [record for record in caplog.records if record.levelno > logging.DEBUG]


def test_first_audio_credential_apply_emits_event(monkeypatch, tmp_path, caplog):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    secret = "sk-test-first-credential-value"
    client = AudioClient({"stt": {"OPENAI_API_KEY": ""}})
    openwebui._reconcile_audio(
        client, client.audio, {"stt": {"MODEL": "asr", "OPENAI_API_KEY": secret}}
    )
    assert any(record.levelno >= logging.WARNING for record in caplog.records)
    _assert_secrets_absent(
        caplog.records, (tmp_path / "managed-audio.json.credentials"), secret
    )


def test_audio_credential_rotation_emits_event_without_persisting_value(
    monkeypatch, tmp_path, caplog
):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    first, rotated = "sk-test-first-credential", "sk-test-rotated-credential"
    client = AudioClient({"stt": {"OPENAI_API_KEY": ""}})
    desired = {"stt": {"MODEL": "asr", "OPENAI_API_KEY": first}}
    openwebui._reconcile_audio(client, client.audio, desired)
    marker = tmp_path / "managed-audio.json.credentials"
    original_marker = marker.read_bytes()
    client.audio["stt"]["OPENAI_API_KEY"] = rotated
    caplog.clear()
    client.audio["stt"]["MODEL"] = "asr-rotated"

    openwebui._reconcile_audio(
        client,
        client.audio,
        {"stt": {"MODEL": "asr-rotated", "OPENAI_API_KEY": rotated}},
    )

    assert any(record.levelno >= logging.WARNING for record in caplog.records)
    assert marker.read_bytes() == original_marker
    _assert_secrets_absent(caplog.records, marker, first, rotated)


def test_released_audio_credential_emits_event_and_removes_marker(
    monkeypatch, tmp_path, caplog
):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    secret = "sk-test-release-credential"
    client = AudioClient({"stt": {"OPENAI_API_KEY": ""}})
    openwebui._reconcile_audio(
        client, client.audio, {"stt": {"MODEL": "asr", "OPENAI_API_KEY": secret}}
    )
    caplog.clear()

    result = openwebui._reconcile_audio(client, client.audio, {})

    assert result["status"] == "verified"
    assert any(record.levelno >= logging.WARNING for record in caplog.records)
    assert not (tmp_path / "managed-audio.json.credentials").exists()
    _assert_secrets_absent(
        caplog.records, tmp_path / "managed-audio.json.credentials", secret
    )


def test_audio_credential_restoration_failure_remains_warning_and_refuses(
    monkeypatch, tmp_path, caplog
):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    monkeypatch.setenv("OMLX_API_KEY", "sk-test-baseline-credential")
    client = AudioClient({"stt": {"OPENAI_API_KEY": "sk-test-baseline-credential"}})
    openwebui._reconcile_audio(
        client,
        client.audio,
        {"stt": {"MODEL": "asr", "OPENAI_API_KEY": "sk-test-managed-credential"}},
    )
    monkeypatch.setenv("OMLX_API_KEY", "sk-test-changed-baseline")
    caplog.clear()

    result = openwebui._reconcile_audio(client, client.audio, {})

    assert result["status"] == "refused"
    assert result["applied"] is False
    assert result["verified"] is False
    assert any(record.levelno >= logging.WARNING for record in caplog.records)
    _assert_secrets_absent(
        caplog.records,
        tmp_path / "managed-audio.json.credentials",
        "sk-test-baseline-credential",
        "sk-test-managed-credential",
        "sk-test-changed-baseline",
    )


def _assert_secrets_absent(records, marker, *secrets):
    messages = "\n".join(record.getMessage() for record in records)
    marker_bytes = marker.read_bytes() if marker.exists() else b""
    for secret in secrets:
        assert secret not in messages
        assert secret.encode() not in marker_bytes
