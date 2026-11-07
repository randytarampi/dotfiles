"""Readiness does not imply live model inference or client acceptance."""

import importlib.util
import json
from pathlib import Path
from urllib.error import HTTPError, URLError

SCRIPT = Path(__file__).resolve().parents[2] / "verify-local-service-readiness.py"
SPEC = importlib.util.spec_from_file_location("local_readiness", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def read(self, _length):
        return b"{}"


def test_loopback_validation_is_strict():
    for endpoint in ("http://127.0.0.1:4000/v1", "http://localhost:11434"):
        assert MODULE.loopback_origin(endpoint)
    for endpoint in (
        "https://127.0.0.1:4000",
        "http://169.254.169.254:4000",
        "http://localhost:4000@evil.test:4000",
        "http://[::1]:99999",
        "http://localhost:4000?redirect=evil",
    ):
        assert MODULE.loopback_origin(endpoint) is None


def test_optional_probe_does_not_send_credentials_or_assume_inference():
    calls = []

    def opener(request, timeout):
        calls.append((request.full_url, request.headers, timeout))
        return Response()

    assert MODULE.probe_http("http://127.0.0.1:4000/health", opener=opener) == (
        "REACHABLE_NOT_INFERENCE_QUALIFIED"
    )
    assert calls == [("http://127.0.0.1:4000/health", {}, 1)]
    assert MODULE.probe_http("http://outside.test:4000/health", opener=opener) == (
        "REFUSED_NON_LOOPBACK"
    )
    assert len(calls) == 1


def test_auth_errors_are_not_consumer_proof():
    def auth_error(request, timeout):
        raise HTTPError(request.full_url, 401, "unauthorized", {}, None)

    def down(_request, timeout):
        raise URLError("disconnected")

    url = "http://127.0.0.1:4000/v1/models"
    assert MODULE.probe_http(url, opener=auth_error) == "REACHABLE_AUTH_REQUIRED"
    assert MODULE.probe_http(url, opener=down) == "UNAVAILABLE"


def test_inventory_is_nonmutating_and_gated(monkeypatch):
    monkeypatch.setattr(
        MODULE,
        "LOCAL_ENGINES",
        {
            "ollama": {
                "base_url": lambda: "http://localhost:11434/v1",
                "gate_env": None,
            },
            "omlx": {
                "base_url": lambda: "http://127.0.0.1:8000/v1",
                "gate_env": "DOTFILES_RUN_OMLX_SETUP",
            },
        },
    )
    env = {"DOTFILES_RUN_OMLX_SETUP": "0"}
    report = MODULE.collect(environ=env)
    assert {item["observation"] for item in report} == {"NOT_PROBED", "GATE_DISABLED"}
    assert all(item["model_ready"] == "UNKNOWN" for item in report)
    assert "litellm" not in json.dumps(report)
    env.update(DOTFILES_RUN_LITELLM_SETUP="1", DOTFILES_USE_LITELLM_PROXY="1")
    report = MODULE.collect(environ=env)
    assert any(item["service"] == "litellm" for item in report)
