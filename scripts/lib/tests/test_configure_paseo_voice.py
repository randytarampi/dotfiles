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
