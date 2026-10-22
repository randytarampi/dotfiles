"""Resolve LiteLLM client credentials without exposing secret material."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

DEFAULT_CLIENT_FILES = {
    "LITELLM_OPENCODE_KEY": "opencode.key",
    "LITELLM_PI_KEY": "pi.key",
}


@dataclass(frozen=True)
class ClientAuth:
    state: str
    value: str | None = None
    detail: str | None = None

    @property
    def authorized(self):
        return self.state == "accepted"


def resolve_client_auth(env_name, key_file=None):
    explicit = key_file or os.environ.get("LITELLM_CLIENT_KEY_FILE", "").strip()
    path = (
        Path(explicit)
        if explicit
        else Path.home()
        / ".local/share/litellm/clients"
        / DEFAULT_CLIENT_FILES.get(
            env_name, f"{env_name.lower().removesuffix('_key')}.key"
        )
    )
    env_value = os.environ.get(env_name, "").strip()
    try:
        file_value = path.read_text(encoding="utf-8").strip()
    except OSError:
        file_value = ""
    if env_value and file_value and env_value != file_value:
        return ClientAuth("conflict", detail="env and file credentials differ")
    value = file_value or env_value
    if not value:
        return ClientAuth("absent", detail=f"no {env_name} client key available")
    return ClientAuth("accepted", value=value)
