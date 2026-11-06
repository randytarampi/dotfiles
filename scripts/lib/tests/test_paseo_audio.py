"""Exact Paseo speech route selection and gateway isolation."""

import json
from unittest.mock import Mock

import pytest

from paseo_audio import choose, eligible

MODELS = [
    {"name": "plain", "model_type": "audio_stt"},
    {"name": "favorite", "model_type": "audio_stt", "is_favorite": True},
    {"name": "pinned", "model_type": "audio_stt", "pinned": True},
    {"name": "voice", "model_type": "audio_tts", "pinned": True, "is_favorite": True},
]


def probe_ok(_endpoint, _alias, **_kwargs):
    return Mock(status="ok")


def test_pinned_priority_and_exact_gateway_alias():
    stt = choose(
        "stt",
        MODELS,
        "http://127.0.0.1:4000/v1",
        "secret",
        served=["omlx/pinned"],
        probe=probe_ok,
    )
    assert stt["wire_model"] == "omlx/pinned"
    assert stt["name"] == "pinned"
    assert eligible(MODELS, "stt", served=[]) == []


def test_previous_and_explicit_win_only_when_qualified():
    previous = choose(
        "stt",
        MODELS,
        "http://127.0.0.1:4000/v1",
        "secret",
        previous="plain",
        probe=probe_ok,
    )
    assert previous["name"] == "plain"
    explicit = choose(
        "stt",
        MODELS,
        "http://127.0.0.1:4000/v1",
        "secret",
        preferred="favorite",
        previous="plain",
        probe=probe_ok,
    )
    assert explicit["name"] == "favorite"


def test_no_protocol_probe_no_qualified_route():
    def fail(*_args, **_kwargs):
        return Mock(status="unavailable")

    with pytest.raises(ValueError, match="no qualified"):
        choose("stt", MODELS, "http://127.0.0.1:4000/v1", "secret", probe=fail)
    with pytest.raises(ValueError, match="failed exact"):
        choose(
            "stt",
            MODELS,
            "http://127.0.0.1:4000/v1",
            "secret",
            preferred="plain",
            probe=fail,
        )


def test_no_cloud_or_unserved_wire_fallback():
    with pytest.raises(ValueError, match="loopback"):
        choose("stt", MODELS, "https://api.openai.com/v1", "secret", probe=probe_ok)
    with pytest.raises(ValueError, match="unavailable"):
        choose(
            "stt",
            MODELS,
            "http://127.0.0.1:4000/v1",
            "secret",
            served=[],
            preferred="pinned",
            probe=probe_ok,
        )


def test_tts_uses_selected_voice():
    calls = []

    def probe(_endpoint, _alias, **kwargs):
        calls.append(kwargs)
        return Mock(status="ok")

    chosen = choose(
        "tts",
        MODELS,
        "http://127.0.0.1:4000/v1",
        "secret",
        voice="serena",
        probe=probe,
    )
    assert chosen["name"] == "voice"
    assert calls[0]["voice"] == "serena"


def test_chatterbox_turbo_is_eligible_and_configurable_exclusion_still_works(
    monkeypatch,
):
    import ai_models

    candidate = [{"name": "chatterbox-turbo-4bit", "model_type": "audio_tts"}]
    assert eligible(candidate, "tts") == [
        ("chatterbox-turbo-4bit", "chatterbox-turbo-4bit")
    ]

    assert ai_models.is_known_unqualified_tts("chatterbox-multilingual-v3")
    assert (
        eligible(
            [{"name": "chatterbox-multilingual-v3", "model_type": "audio_tts"}], "tts"
        )
        == []
    )

    monkeypatch.setattr(ai_models, "KNOWN_UNQUALIFIED_TTS_PREFIXES", ("blocked-tts",))
    assert ai_models.is_known_unqualified_tts("blocked-tts-model")
    assert (
        eligible([{"name": "blocked-tts-model", "model_type": "audio_tts"}], "tts")
        == []
    )


def test_stale_previous_model_falls_back_to_qualified_pinned_candidate():
    def probe(_endpoint, alias, **_kwargs):
        return Mock(status="unavailable" if alias == "plain" else "ok")

    chosen = choose(
        "stt",
        MODELS,
        "http://127.0.0.1:4000/v1",
        "secret",
        previous="plain",
        probe=probe,
    )
    assert chosen["name"] == "pinned"


def test_saved_wire_alias_is_resolved_to_canonical_model_name():
    chosen = choose(
        "stt",
        MODELS,
        "http://127.0.0.1:4000/v1",
        "secret",
        served=["omlx/pinned", "omlx/favorite"],
        previous="omlx/favorite",
        probe=probe_ok,
    )
    assert chosen["name"] == "favorite"


def test_loopback_prefix_is_not_enough_to_protect_speech_credentials():
    for endpoint in (
        "http://localhost:80@evil.example/v1",
        "http://127.0.0.1:80@evil.example/v1",
        "http://127.0.0.1.evil.example:80/v1",
        "https://api.openai.com/v1",
        "http://192.168.1.7:8765/v1",
    ):
        with pytest.raises(ValueError, match="loopback"):
            choose("stt", MODELS, endpoint, "secret", probe=probe_ok)


def test_paseo_tts_never_accepts_a_different_voice_from_probe(monkeypatch):
    from route_probes import probe_tts

    calls = []

    def request(_endpoint, body, _key, _timeout):
        speaker = json.loads(body)["voice"]
        calls.append(speaker)
        if speaker == "serena":
            return (
                500,
                {},
                b'{"error":{"message":"Speaker invalid. Available: [Vivian]"}}',
                None,
            )
        return 200, {"Content-Type": "audio/wav"}, b"RIFF", None

    monkeypatch.setattr("route_probes._request", request)
    rejected = probe_tts(
        "http://127.0.0.1:8765/v1",
        "tts-model",
        key_value="local",
        voice="serena",
        strict_voice=True,
    )
    assert rejected.status == "unavailable"
    assert calls == ["serena"]

    legacy = probe_tts(
        "http://127.0.0.1:8765/v1",
        "tts-model",
        key_value="local",
        voice="serena",
    )
    assert legacy.status == "ok"
    assert calls[-2:] == ["serena", "Vivian"]


def test_paseo_choose_passes_strict_voice_to_exact_tts_probe():
    calls = []

    def probe(_endpoint, _alias, **kwargs):
        calls.append(kwargs)
        return Mock(status="unavailable")

    with pytest.raises(ValueError, match="no qualified"):
        choose(
            "tts",
            MODELS,
            "http://127.0.0.1:8765/v1",
            "local",
            voice="serena",
            probe=probe,
        )
    assert len(calls) == 1
    assert calls[0]["strict_voice"] is True
    assert calls[0]["voice"] == "serena"


def _voice_configurer():
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "configure-paseo-voice.py"
    spec = importlib.util.spec_from_file_location("paseo_voice_adapter_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_persisted_paseo_site_override_must_match_qualified_route():
    module = _voice_configurer()
    endpoint = "http://127.0.0.1:8765/v1"
    selections = {
        "stt": {"wire_model": "stt-local"},
        "tts": {"wire_model": "tts-local"},
    }
    config = {
        "features": {
            "dictation": {"stt": {"baseUrl": endpoint}},
            "voiceMode": {"stt": {}, "tts": {"voice": "serena"}},
        }
    }
    module.validate_persisted_routes(config, selections, endpoint, "key", "serena")
    config["features"]["voiceMode"]["tts"]["baseUrl"] = "https://api.openai.com/v1"
    with pytest.raises(ValueError, match="persisted speech override"):
        module.validate_persisted_routes(config, selections, endpoint, "key", "serena")
    config["features"]["voiceMode"]["tts"]["baseUrl"] = endpoint
    config["features"]["dictation"]["stt"]["apiKey"] = "cloud-key"
    with pytest.raises(ValueError, match="persisted speech override"):
        module.validate_persisted_routes(config, selections, endpoint, "key", "serena")


def test_private_adapter_rejects_symlinked_ancestor(tmp_path):
    module = _voice_configurer()
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "redirect"
    link.symlink_to(real, target_is_directory=True)
    target = link / "nested" / "dotfiles-voice.env"
    with pytest.raises(ValueError, match="symlink"):
        module.write_new(target, "KEY=secret\n")
    assert not (real / "nested").exists()
