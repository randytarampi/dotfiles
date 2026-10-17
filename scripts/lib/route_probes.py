"""Small, explicit inference probes for OpenAI-compatible engine routes."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass

from selected_service import Qualification


@dataclass(frozen=True)
class ProbeResult:
    status: str
    model: str
    alias: str
    endpoint: str
    notes: tuple[str, ...] = ()
    dimension: int | None = None
    auth_failure: str | None = None


def local_endpoint_for(provider, protocol="openai"):
    """Reuse local-engine gate and endpoint policy without probing discovery."""
    from local_engines import local_endpoint_for as resolve

    return resolve(provider, protocol)


def litellm_proxy_endpoint_for(gateway_active=True):
    """Return the LiteLLM OpenAI-compatible endpoint when its gate is active."""
    return "http://127.0.0.1:4000/v1" if gateway_active else None


def _wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\0\0" * 800)
    return buffer.getvalue()


def _request(url, payload, key_value, timeout, content_type="application/json"):
    headers = {"Content-Type": content_type}
    if key_value:
        headers["Authorization"] = "Bearer " + key_value
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, response.headers, response.read(), None
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read(), None
    except (urllib.error.URLError, TimeoutError, OSError):
        return None, {}, b"", "connection unavailable or timed out"


def _result(status, model, endpoint, notes=(), auth_failure=None, dimension=None):
    return ProbeResult(
        status, model, model, endpoint, tuple(notes), dimension, auth_failure
    )


def _status_result(code, model, endpoint):
    if code is None:
        return _result(
            "unknown", model, endpoint, ("connection unavailable or timed out",)
        )
    if code in (401, 403):
        return _result(
            "unknown",
            model,
            endpoint,
            ("authentication rejected",),
            "unauthorized" if code == 401 else "forbidden",
        )
    if code != 200:
        return _result("unavailable", model, endpoint, ("HTTP request rejected",))
    return None


def _multipart(model, key_value, timeout):
    boundary = "route-probe-boundary"
    body = (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\n{model}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="probe.wav"\r\n'
            "Content-Type: audio/wav\r\n\r\n"
        ).encode()
        + _wav_bytes()
        + f"\r\n--{boundary}--\r\n".encode()
    )
    return _request, body, "multipart/form-data; boundary=" + boundary


def probe_stt(base_url, model, key_reference, key_value=None, timeout=5):
    endpoint = base_url.rstrip("/") + "/audio/transcriptions"
    request, body, content_type = _multipart(model, key_value, timeout)
    code, _, data, _ = request(endpoint, body, key_value, timeout, content_type)
    failed = _status_result(code, model, endpoint)
    if failed:
        return failed
    try:
        parsed = json.loads(data.decode("utf-8"))
        text = parsed.get("text")
        if not isinstance(text, str):
            raise ValueError
    except (ValueError, UnicodeDecodeError, AttributeError):
        return _result(
            "response_shape_error",
            model,
            endpoint,
            ("expected a transcription text field",),
        )
    # A silent probe clip may transcribe to an empty string; the shape
    # (multipart accepted, JSON with a text field) is the protocol evidence.
    return _result("ok", model, endpoint, ("HTTP 200 transcription response",))


def _available_voices(data):
    """Parse the server's own speaker guidance from a 500 body, if given."""
    try:
        message = json.loads(data.decode("utf-8"))["error"]["message"]
    except (ValueError, UnicodeDecodeError, KeyError, TypeError):
        return []
    if "Speaker" not in message or "Available" not in message:
        return []
    try:
        listed = message.split("Available:", 1)[1].strip().strip("[]")
        return [name.strip().strip("'") for name in listed.split(",") if name.strip()]
    except (IndexError, AttributeError):
        return []


def probe_tts(
    base_url, model, voice=None, key_reference=None, key_value=None, timeout=5
):
    endpoint = base_url.rstrip("/") + "/audio/speech"
    attempts = voice or "alloy"
    notes = []
    for _attempt in range(2):
        payload = {"model": model, "input": "probe", "voice": attempts}
        code, headers, data, _ = _request(
            endpoint, json.dumps(payload).encode(), key_value, timeout
        )
        failed = _status_result(code, model, endpoint)
        if failed:
            voices = _available_voices(data) if code == 500 else []
            if not voices or _attempt:
                return failed
            attempts = voices[0]
            notes.append(f"server-recommended voice discovered: {attempts}")
            continue
        if not headers.get("Content-Type", "").lower().startswith("audio/") or not data:
            return _result(
                "response_shape_error",
                model,
                endpoint,
                ("expected non-empty audio/* response",),
            )
        return _result(
            "ok", model, endpoint, tuple(notes) or ("HTTP 200 binary audio response",)
        )
    return _result("unavailable", model, endpoint, ("voice retry exhausted",))


def probe_embeddings(base_url, model, key_reference, key_value=None, timeout=5):
    endpoint = base_url.rstrip("/") + "/embeddings"
    payload = {"model": model, "input": "route probe deterministic seed"}
    code, _, data, _ = _request(
        endpoint, json.dumps(payload).encode(), key_value, timeout
    )
    failed = _status_result(code, model, endpoint)
    if failed:
        return failed
    try:
        vector = json.loads(data.decode("utf-8"))["data"][0]["embedding"]
        if (
            not isinstance(vector, list)
            or not vector
            or any(type(value) not in (int, float) for value in vector)
        ):
            raise ValueError
    except (ValueError, UnicodeDecodeError, KeyError, IndexError, TypeError):
        return _result(
            "response_shape_error",
            model,
            endpoint,
            ("expected numeric embedding vector",),
        )
    return _result(
        "ok",
        model,
        endpoint,
        ("HTTP 200 numeric embedding vector",),
        dimension=len(vector),
    )


def qualification_from_probe(result):
    if result.status == "ok":
        return Qualification.PROTOCOL_PROBED
    if result.status == "response_shape_error" or result.status == "unavailable":
        return Qualification.UNAVAILABLE
    return Qualification.UNKNOWN
