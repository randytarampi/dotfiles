import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "configure-paseo-voice.py"
SPEC = importlib.util.spec_from_file_location("configure_paseo_voice", SCRIPT)
voice = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(voice)


@pytest.mark.parametrize(
    "override, value",
    [
        ("OMLX_BASE_URL", "https://external.example"),
        ("OMLX_HOST", "external.example"),
        ("OMLX_BASE_URL", "ftp://127.0.0.1:8000"),
        ("OMLX_BASE_URL", "http://127.0.0.1"),
        ("OMLX_BASE_URL", "http://user:pass@127.0.0.1:8000"),
        ("OMLX_BASE_URL", "http://127.0.0.1:8000/#fragment"),
        ("OMLX_BASE_URL", "http://8.8.8.8:8000"),
    ],
)
def test_probe_refuses_nonlocal_origin_before_network(monkeypatch, override, value):
    for name in ("OMLX_BASE_URL", "OMLX_HOST", "OMLX_PORT", "OMLX_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(voice.GATE, "1")
    monkeypatch.setenv("OMLX_API_KEY", "synthetic-test-key")
    monkeypatch.setenv(override, value)
    calls = []
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(
        voice, "list_omlx_models", lambda **kwargs: calls.append(kwargs)
    )

    assert voice.main(["--probe-routes"]) == 1
    assert calls == []


@pytest.mark.parametrize(
    "origin", ["http://127.0.0.1:8000", "http://localhost:8000", "http://[::1]:8000"]
)
def test_loopback_origin_reaches_discovery(monkeypatch, origin):
    for name in ("OMLX_BASE_URL", "OMLX_HOST", "OMLX_PORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(voice.GATE, "1")
    monkeypatch.setenv("OMLX_BASE_URL", origin)
    calls = []
    monkeypatch.setattr(
        voice, "list_omlx_models", lambda **kwargs: calls.append(kwargs) or []
    )

    assert voice.main(["--probe-routes"]) == 1
    assert calls == [{"strict": True}]


def test_native_local_vad_preserves_exact_direct_omlx_stt_tts_routes():
    selection = {
        "stt": {"wire_model": "Qwen3-ASR-1.7B-8bit"},
        "tts": {"wire_model": "Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit"},
    }
    rendered = voice.render(
        selection,
        "http://127.0.0.1:11427/v1",
        "omlx-local",
        "serena",
        "/tmp/synthetic-paseo-models",
    )
    fields = dict(line.split("=", 1) for line in rendered.splitlines()[1:])

    assert fields["PASEO_VOICE_TURN_DETECTION_PROVIDER"] == "local"
    assert fields["PASEO_LOCAL_MODELS_DIR"] == "/tmp/synthetic-paseo-models"
    assert fields["PASEO_DICTATION_STT_PROVIDER"] == "openai"
    assert fields["PASEO_VOICE_STT_PROVIDER"] == "openai"
    assert fields["PASEO_VOICE_TTS_PROVIDER"] == "openai"
    assert fields["OPENAI_STT_BASE_URL"] == "http://127.0.0.1:11427/v1"
    assert fields["OPENAI_TTS_BASE_URL"] == "http://127.0.0.1:11427/v1"
    assert fields["STT_MODEL"] == "Qwen3-ASR-1.7B-8bit"
    assert fields["TTS_MODEL"] == "Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit"
    assert fields["TTS_VOICE"] == "serena"
    assert fields["OPENAI_STT_API_KEY"] == "omlx-local"
    assert fields["OPENAI_TTS_API_KEY"] == "omlx-local"
    assert "PASEO_VOICE_TURN_DETECTION_PROVIDER='openai'" not in rendered


def test_existing_private_adapter_is_not_replaced_for_local_vad(tmp_path):
    path = tmp_path / "dotfiles-voice.env"
    path.write_text("# Managed Paseo speech adapter (owner-only)\n", encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(ValueError, match="differs"):
        voice.write_new(
            path,
            "# Managed Paseo speech adapter (owner-only)\nPASEO_VOICE_TURN_DETECTION_PROVIDER=local\n",
        )
    assert (
        path.read_text(encoding="utf-8")
        == "# Managed Paseo speech adapter (owner-only)\n"
    )


@pytest.mark.parametrize(
    "value",
    [None, "", "relative/path", "https://example.org/models", "/tmp/models\nBAD=1"],
)
def test_local_vad_worker_requires_explicit_safe_absolute_models_dir(value):
    with pytest.raises(ValueError, match="local VAD models directory"):
        voice.validate_local_vad_models_dir(value)


def test_local_vad_worker_refuses_symlinked_models_path(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "redirect"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="non-symlinked"):
        voice.validate_local_vad_models_dir(str(link / "models"))


def test_local_vad_worker_can_reuse_explicit_private_paseo_models_dir(tmp_path):
    home = tmp_path / "paseo"
    models = home / "models" / "local-speech"
    assert voice.validate_local_vad_models_dir(str(models)) == str(models)
    assert not models.exists()
