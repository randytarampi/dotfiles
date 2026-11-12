#!/usr/bin/env python3
"""Explicitly qualify local Paseo speech endpoints and write a private env adapter."""

import argparse
import os
from pathlib import Path
import shlex
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from cli_helpers import add_common_args  # noqa: E402
from constants import get_litellm_proxy_mode, get_omlx_base_url  # noqa: E402
from local_engines import local_endpoint_for  # noqa: E402
from omlx import list_omlx_models  # noqa: E402
from paseo_audio import MODEL_ID, choose, is_loopback_audio_endpoint  # noqa: E402
from paseo_web_policy import read_proxy_config  # noqa: E402
from route_probes import fetch_gateway_models  # noqa: E402

GATE = "DOTFILES_RUN_PASEO_VOICE_SETUP"
ENV_NAME = "dotfiles-voice.env"


def read_key(path):
    """Never read an unowned, symlinked or group-readable client secret."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("Paseo key file missing or symlinked")
    info = path.stat()
    if (
        info.st_uid != os.getuid()
        or info.st_nlink != 1
        or not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise ValueError("Paseo key file has unsafe permissions")
    value = path.read_text(encoding="utf-8").strip()
    if not value or "\n" in value or "\r" in value:
        raise ValueError("Paseo key material missing or malformed")
    return value


def settings_path():
    return Path.home() / ".local/share/paseo" / ENV_NAME


def private_env_contents(path):
    """Read only an exclusively owned private environment file."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("existing speech env must be a regular private file")
    info = path.stat()
    if (
        info.st_uid != os.getuid()
        or info.st_nlink != 1
        or not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise ValueError("existing speech env has unsafe permissions")
    return path.read_text(encoding="utf-8")


def previous_models(path):
    if not path.exists() and not path.is_symlink():
        return {}
    content = private_env_contents(path)
    if not content.startswith("# Managed Paseo speech adapter"):
        raise ValueError("existing speech adapter is user-owned")
    previous = {}
    for line in content.splitlines():
        for name in ("STT_MODEL", "TTS_MODEL"):
            if line.startswith(name + "="):
                value = line.split("=", 1)[1].strip("'")
                if MODEL_ID.fullmatch(value):
                    previous[name] = value
    return previous


def validate_local_vad_models_dir(value):
    """A local turn detector needs a real local provider worker configuration."""
    if not isinstance(value, str) or not value or any(c in value for c in "\r\n\x00"):
        raise ValueError("local VAD models directory must be explicitly configured")
    path = Path(value).expanduser()
    if (
        not path.is_absolute()
        or path.is_symlink()
        or any(parent.is_symlink() for parent in path.parents)
    ):
        raise ValueError(
            "local VAD models directory must be an absolute non-symlinked path"
        )
    if path.exists() and not path.is_dir():
        raise ValueError("local VAD models directory must be a directory")
    return str(path)


def render(selections, route, key, voice, local_vad_models_dir):
    local_vad_models_dir = validate_local_vad_models_dir(local_vad_models_dir)
    values = {
        "PASEO_DICTATION_STT_PROVIDER": "openai",
        # Paseo v0.15.4 has a local Sherpa/Silero worker for realtime VAD.
        # STT/TTS remain on the separately qualified direct oMLX route.
        "PASEO_VOICE_TURN_DETECTION_PROVIDER": "local",
        # Paseo v0.15.4 only creates the local VAD worker when this config is set.
        "PASEO_LOCAL_MODELS_DIR": local_vad_models_dir,
        "PASEO_VOICE_STT_PROVIDER": "openai",
        "PASEO_VOICE_TTS_PROVIDER": "openai",
        "OPENAI_STT_API_KEY": key,
        "OPENAI_STT_BASE_URL": route,
        "OPENAI_TTS_API_KEY": key,
        "OPENAI_TTS_BASE_URL": route,
        "STT_MODEL": selections["stt"]["wire_model"],
        "TTS_MODEL": selections["tts"]["wire_model"],
        "TTS_VOICE": voice,
    }
    for name, value in values.items():
        if not isinstance(value, str) or any(ch in value for ch in "\r\n\x00"):
            raise ValueError("invalid value in private speech environment")
    return "# Managed Paseo speech adapter (owner-only)\n" + "".join(
        name + "=" + shlex.quote(value) + "\n" for name, value in values.items()
    )


def validate_persisted_routes(config, selections, endpoint, key, voice):
    """Stored Paseo speech options outrank daemon environment; refuse drift."""
    features = config.get("features", {})
    if not isinstance(features, dict):
        raise ValueError("Paseo features must be an object")
    sites = (
        (("dictation", "stt"), "stt"),
        (("voiceMode", "stt"), "stt"),
        (("voiceMode", "tts"), "tts"),
    )
    for keys, operation in sites:
        node = features
        for name in keys:
            node = node.get(name, {}) if isinstance(node, dict) else None
        if not isinstance(node, dict):
            raise ValueError("Paseo persisted speech settings are malformed")
        expected = {
            "apiKey": key,
            "baseUrl": endpoint,
            "model": selections[operation]["wire_model"],
        }
        if operation == "tts":
            expected["voice"] = voice
        for name, wanted in expected.items():
            if name in node and node[name] != wanted:
                raise ValueError(
                    "Paseo persisted speech override conflicts: "
                    + ".".join(keys)
                    + "."
                    + name
                )


def write_new(path, content):
    directory = path.parent
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("refusing symlink in Paseo speech path")
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = directory.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("Paseo speech directory must be private")
    if path.exists() or path.is_symlink():
        if private_env_contents(path) == content:
            return
        raise ValueError("existing speech env differs; manual reconciliation required")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        output.write(content)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    add_common_args(parser)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--probe-routes", action="store_true")
    parser.add_argument("--stt-model")
    parser.add_argument("--tts-model")
    parser.add_argument(
        "--local-vad-models-dir", default=os.environ.get("PASEO_LOCAL_MODELS_DIR")
    )
    parser.add_argument(
        "--voice", default=os.environ.get("DOTFILES_VOICE_TTS_VOICE") or "serena"
    )
    args = parser.parse_args(argv)
    try:
        if not args.probe_routes:
            print(
                "Speech routes UNQUALIFIED; use --probe-routes for local audio inference"
            )
            return 0 if not args.apply else 1
        if os.environ.get(GATE) != "1":
            raise ValueError("speech route probes require explicit " + GATE + "=1")
        if args.apply:
            local_vad_models_dir = validate_local_vad_models_dir(
                args.local_vad_models_dir
            )
        else:
            local_vad_models_dir = None
        if not MODEL_ID.fullmatch(args.voice):
            raise ValueError("invalid TTS voice")
        if not is_loopback_audio_endpoint(get_omlx_base_url()):
            raise ValueError("Paseo oMLX discovery must be loopback-only")
        models = list_omlx_models(strict=True)
        if not models:
            raise ValueError("no inference-visible oMLX models")
        gateway = get_litellm_proxy_mode()
        if gateway:
            key_path = Path.home() / ".local/share/litellm/clients/paseo.key"
            key = read_key(key_path)
            port = os.environ.get("LITELLM_PORT", "4000")
            if not port.isdecimal() or not 1 <= int(port) <= 65535:
                raise ValueError("invalid LiteLLM port")
            endpoint = "http://127.0.0.1:" + port + "/v1"
            served = fetch_gateway_models(endpoint, key)
            if served is None:
                raise ValueError("LiteLLM served model inventory unavailable")
        else:
            engine = local_endpoint_for("omlx", "openai")
            if not engine:
                raise ValueError("oMLX route unavailable")
            endpoint = engine[0]
            key = os.environ.get("OMLX_API_KEY") or "omlx-local"
            served = None
        prior = previous_models(settings_path())
        selection = {
            "stt": choose(
                "stt",
                models,
                endpoint,
                key,
                served,
                args.stt_model,
                prior.get("STT_MODEL"),
            ),
            "tts": choose(
                "tts",
                models,
                endpoint,
                key,
                served,
                args.tts_model,
                prior.get("TTS_MODEL"),
                voice=args.voice,
            ),
        }
        print("Qualified STT: " + selection["stt"]["wire_model"])
        print("Qualified TTS: " + selection["tts"]["wire_model"])
        if not args.apply or args.dry_run:
            return 0
        validate_persisted_routes(
            read_proxy_config(), selection, endpoint, key, args.voice
        )
        write_new(
            settings_path(),
            render(selection, endpoint, key, args.voice, local_vad_models_dir),
        )
        print("Private speech adapter written; inspect daemon env inheritance")
        return 0
    except (OSError, ValueError) as error:
        print("Paseo speech qualification refused: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
