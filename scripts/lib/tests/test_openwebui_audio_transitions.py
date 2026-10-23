import json
import logging
import hashlib

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
    calls = []
    monkeypatch.setattr(
        openwebui,
        "_write_audio_credential_transitions",
        lambda value: calls.append(value),
    )
    client = AudioClient({"stt": {"OPENAI_API_KEY": ""}})
    desired = {"stt": {"MODEL": "asr", "OPENAI_API_KEY": "managed-key"}}

    openwebui._reconcile_audio(client, client.audio, desired)
    assert len(calls) == 1
    caplog.clear()
    monkeypatch.setattr(
        openwebui,
        "_read_audio_credential_transitions",
        lambda: calls[0],
    )

    openwebui._reconcile_audio(client, client.audio, desired)

    assert len(calls) == 1
    assert not [record for record in caplog.records if record.levelno > logging.DEBUG]


def test_first_audio_credential_apply_emits_event(monkeypatch, tmp_path, caplog):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    secret = "sk-test-first-credential-value"
    calls = []
    monkeypatch.setattr(
        openwebui,
        "_write_audio_credential_transitions",
        lambda value: calls.append(value),
    )
    client = AudioClient({"stt": {"OPENAI_API_KEY": ""}})
    openwebui._reconcile_audio(
        client, client.audio, {"stt": {"MODEL": "asr", "OPENAI_API_KEY": secret}}
    )
    assert any(record.levelno >= logging.WARNING for record in caplog.records)
    assert len(calls) == 1
    _assert_secrets_absent(
        caplog.records, (tmp_path / "managed-audio.json.credentials"), secret
    )


def test_audio_credential_rotation_emits_event_without_persisting_value(
    monkeypatch, tmp_path, caplog
):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    first, rotated = "sk-test-first-credential", "sk-test-rotated-credential"
    calls = []
    monkeypatch.setattr(
        openwebui,
        "_write_audio_credential_transitions",
        lambda value: calls.append(value),
    )
    client = AudioClient({"stt": {"OPENAI_API_KEY": ""}})
    desired = {"stt": {"MODEL": "asr", "OPENAI_API_KEY": first}}
    openwebui._reconcile_audio(client, client.audio, desired)
    assert len(calls) == 1
    client.audio["stt"]["OPENAI_API_KEY"] = rotated
    caplog.clear()
    client.audio["stt"]["MODEL"] = "asr-rotated"

    openwebui._reconcile_audio(
        client,
        client.audio,
        {"stt": {"MODEL": "asr-rotated", "OPENAI_API_KEY": rotated}},
    )

    assert len(calls) == 1
    assert any(record.levelno >= logging.WARNING for record in caplog.records)
    _assert_secrets_absent(
        caplog.records, tmp_path / "managed-audio.json.credentials", first, rotated
    )


def test_released_audio_credential_emits_event_and_removes_marker(
    monkeypatch, tmp_path, caplog
):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    secret = "sk-test-release-credential"
    calls = []
    monkeypatch.setattr(
        openwebui,
        "_write_audio_credential_transitions",
        lambda value: calls.append(value),
    )
    client = AudioClient({"stt": {"OPENAI_API_KEY": ""}})
    openwebui._reconcile_audio(
        client, client.audio, {"stt": {"MODEL": "asr", "OPENAI_API_KEY": secret}}
    )
    assert len(calls) == 1
    caplog.clear()

    result = openwebui._reconcile_audio(client, client.audio, {})

    assert len(calls) == 1
    assert result["status"] == "verified"
    assert result["verified"] is True
    _assert_secrets_absent(
        caplog.records, tmp_path / "managed-audio.json.credentials", secret
    )


def test_audio_credential_restoration_failure_remains_warning_and_refuses(
    monkeypatch, tmp_path, caplog
):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    monkeypatch.setenv("OMLX_API_KEY", "sk-test-baseline-credential")
    calls = []
    monkeypatch.setattr(
        openwebui,
        "_write_audio_credential_transitions",
        lambda value: calls.append(value),
    )
    client = AudioClient({"stt": {"OPENAI_API_KEY": "sk-test-baseline-credential"}})
    openwebui._reconcile_audio(
        client,
        client.audio,
        {"stt": {"MODEL": "asr", "OPENAI_API_KEY": "sk-test-managed-credential"}},
    )
    monkeypatch.setenv("OMLX_API_KEY", "sk-test-changed-baseline")
    monkeypatch.setattr(
        openwebui,
        "_read_audio_credential_transitions",
        lambda: {
            "stt": {
                "field": "OPENAI_API_KEY",
                "restoration": "env:OMLX_API_KEY",
                "baseline_sha256": hashlib.sha256(
                    b"sk-test-baseline-credential"
                ).hexdigest(),
            }
        },
    )
    caplog.clear()

    result = openwebui._reconcile_audio(client, client.audio, {})

    assert len(calls) == 1
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


def test_rotation_marker_writer_spy_call_counts(monkeypatch, tmp_path):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    calls = []
    real_write = openwebui._write_audio_credential_transitions
    monkeypatch.setattr(
        openwebui,
        "_write_audio_credential_transitions",
        lambda marker: (calls.append(marker), real_write(marker))[1],
    )
    client = AudioClient({"stt": {"OPENAI_API_KEY": ""}})
    first = {"stt": {"MODEL": "asr", "OPENAI_API_KEY": "managed-one"}}
    openwebui._reconcile_audio(client, client.audio, first)
    assert len(calls) == 1
    monkeypatch.setattr(
        openwebui,
        "_read_audio_credential_transitions",
        lambda: calls[0],
    )
    openwebui._reconcile_audio(client, client.audio, first)
    assert len(calls) == 1

    client.audio["stt"]["OPENAI_API_KEY"] = "managed-two"
    openwebui._reconcile_audio(
        client, client.audio, {"stt": {"MODEL": "asr", "OPENAI_API_KEY": "managed-two"}}
    )
    assert len(calls) == 1
    openwebui._reconcile_audio(client, client.audio, {})
    assert len(calls) == 3


def test_unchanged_unowned_credential_is_not_marked_and_can_release(
    monkeypatch, tmp_path
):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    calls = []
    real_write = openwebui._write_audio_credential_transitions
    monkeypatch.setattr(
        openwebui,
        "_write_audio_credential_transitions",
        lambda marker: (calls.append(marker), real_write(marker))[1],
    )
    client = AudioClient({"stt": {"MODEL": "asr", "OPENAI_API_KEY": "existing"}})
    desired = {"stt": {"MODEL": "asr", "OPENAI_API_KEY": "existing"}}

    openwebui._reconcile_audio(client, client.audio, desired)
    assert calls == []
    openwebui._reconcile_audio(client, client.audio, {})
    assert calls == []
    assert client.audio["stt"]["OPENAI_API_KEY"] == "existing"


def test_rotating_client_adopts_unchanged_credential_then_rotates_and_releases(
    monkeypatch, tmp_path
):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    calls = []
    real_write = openwebui._write_audio_credential_transitions
    monkeypatch.setattr(
        openwebui,
        "_write_audio_credential_transitions",
        lambda marker: (calls.append(marker), real_write(marker))[1],
    )
    client = AudioClient({"stt": {"MODEL": "", "OPENAI_API_KEY": "stable"}})

    openwebui._reconcile_audio(
        client, client.audio, {"stt": {"MODEL": "", "OPENAI_API_KEY": "stable"}}
    )
    assert calls == []
    client.audio["stt"]["OPENAI_API_KEY"] = "rotated"
    monkeypatch.setenv("LITELLM_OPENWEBUI_KEY", "rotated")
    openwebui._reconcile_audio(
        client, client.audio, {"stt": {"MODEL": "", "OPENAI_API_KEY": "new"}}
    )
    assert len(calls) == 1
    assert "rotated" not in repr(calls[0]) and "new" not in repr(calls[0])
    result = openwebui._reconcile_audio(client, client.audio, {})
    assert result["status"] == "verified"
    assert calls[1:] == [{}, {}]
    assert client.audio["stt"]["OPENAI_API_KEY"] == "rotated"


def _assert_secrets_absent(records, marker, *secrets):
    messages = "\n".join(record.getMessage() for record in records)
    marker_bytes = marker.read_bytes() if marker.exists() else b""
    for secret in secrets:
        assert secret not in messages
        assert secret.encode() not in marker_bytes
