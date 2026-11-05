"""Qualify Paseo STT/TTS on the exact selected local or gateway route."""

import ipaddress
import re
from urllib.parse import urlsplit

from omlx import prefer_pinned_omlx_audio
from route_probes import probe_stt, probe_tts

MODEL_ID = re.compile(r"[A-Za-z0-9._/:+-]+\Z")
OPERATIONS = {"stt": "audio_stt", "tts": "audio_tts"}


def eligible(models, operation, served=None):
    """Return preference-ranked typed, inference-visible models and wire IDs."""
    expected_type = OPERATIONS[operation]
    candidates = []
    for model in prefer_pinned_omlx_audio(models):
        name = model.get("name")
        if (
            not isinstance(name, str)
            or not MODEL_ID.fullmatch(name)
            or model.get("model_type") != expected_type
        ):
            continue
        if operation == "tts":
            from ai_models import is_known_unqualified_tts

            if is_known_unqualified_tts(name):
                continue
        if served is None:
            wire = name
        elif name in served:
            wire = name
        elif "omlx/" + name in served:
            wire = "omlx/" + name
        else:
            continue
        candidates.append((name, wire))
    return candidates


def is_loopback_audio_endpoint(endpoint):
    """Reject user-info tricks and all nonlocal credential destinations."""
    try:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "http"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is None
            or parsed.query
            or parsed.fragment
        ):
            return False
        host = parsed.hostname
        return host == "localhost" or bool(
            host and ipaddress.ip_address(host).is_loopback
        )
    except (TypeError, ValueError):
        return False


def choose(
    operation,
    models,
    endpoint,
    key,
    served=None,
    preferred=None,
    previous=None,
    voice="serena",
    probe=None,
):
    """Never select a candidate until the exact audio protocol has been probed."""
    if not is_loopback_audio_endpoint(endpoint):
        raise ValueError("Paseo audio route must be loopback-only")
    candidates = eligible(models, operation, served)
    ids = [name for name, _ in candidates]
    if preferred and preferred not in ids:
        raise ValueError("explicit Paseo audio model is unavailable")
    if previous:
        # The persisted adapter stores wire aliases, not always bare model IDs.
        previous = next(
            (name for name, wire in candidates if previous in (name, wire)), None
        )
    ordered = []
    for selected in (preferred, previous):
        if selected and selected not in ordered:
            ordered.append(selected)
    ordered.extend(name for name in ids if name not in ordered)
    by_name = dict(candidates)
    probe = probe or (probe_stt if operation == "stt" else probe_tts)
    for name in ordered:
        alias = by_name[name]
        kwargs = {"key_reference": "LITELLM_PASEO_KEY", "key_value": key}
        if operation == "tts":
            kwargs["voice"] = voice
            kwargs["strict_voice"] = True
        result = probe(endpoint, alias, **kwargs)
        if result.status == "ok":
            return {"name": name, "wire_model": alias, "endpoint": endpoint}
        if preferred and name == preferred:
            raise ValueError("explicit speech model failed exact route probe")
    raise ValueError("no qualified Paseo " + operation.upper() + " route")
