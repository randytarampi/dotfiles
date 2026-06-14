#!/usr/bin/env python3
"""Shared constants for AI configuration scripts.

Centralizes provider base URLs and other constants used across
configure-opencode.py, configure-opencode-voice.py, configure-smallcode.py,
configure-meridian.py, and generate-jetbrains-profiles.py.

`BASE_URLS` only contains cloud providers (`ollama-cloud`, `openai`,
`anthropic`). Use `get_provider_base_url()` and `PROVIDER_BASE_URL_ENVS` for
provider-aware overrides. Meridian and local Ollama base URLs must be built
dynamically via `get_meridian_base_url()` and `get_ollama_local_base_url()`;
`OLLAMA_HOST` is respected at runtime for local Ollama.
"""

# ── Local Ollama defaults ──────────────────────────────────────────────
OLLAMA_LOCAL_HOST_ENV = "OLLAMA_LOCAL_HOST"
OLLAMA_LOCAL_PORT_ENV = "OLLAMA_LOCAL_PORT"
OLLAMA_LOCAL_DEFAULT_HOST = "localhost"
OLLAMA_LOCAL_DEFAULT_PORT = "11434"

# ── Meridian proxy defaults ─────────────────────────────────────────────
MERIDIAN_HOST_ENV = "MERIDIAN_HOST"
MERIDIAN_PORT_ENV = "MERIDIAN_PORT"
MERIDIAN_DEFAULT_HOST = "127.0.0.1"
MERIDIAN_DEFAULT_PORT = "3456"
OLLAMA_HOST_ENV = "OLLAMA_HOST"

# ── Provider base URLs ──────────────────────────────────────────────────
# Single source of truth for all configure scripts.
# Composite URLs are built from the host/port defaults above.
BASE_URLS = {
    "ollama-cloud": "https://ollama.com/v1",
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
}

PROVIDER_BASE_URL_ENVS = {
    "ollama-cloud": "OLLAMA_CLOUD_BASE_URL",
    "openai": "OPENAI_BASE_URL",
    "anthropic": "ANTHROPIC_BASE_URL",
}


def get_meridian_base_url():
    """Build Meridian proxy base URL from env vars with defaults.

    ANTHROPIC_BASE_URL takes priority (official Anthropic SDK env var
    that also signals Meridian proxy usage). Falls back to
    MERIDIAN_HOST/MERIDIAN_PORT.
    """
    import os

    anthropic_override = os.environ.get("ANTHROPIC_BASE_URL", "").strip()
    if anthropic_override:
        return anthropic_override.rstrip("/")

    host = os.environ.get(MERIDIAN_HOST_ENV, MERIDIAN_DEFAULT_HOST)
    port = os.environ.get(MERIDIAN_PORT_ENV, MERIDIAN_DEFAULT_PORT)
    return f"http://{host}:{port}/v1"


def get_ollama_local_base_url():
    """Build local Ollama base URL from env vars with defaults.

    OLLAMA_HOST (official Ollama env var, includes scheme+host[:port])
    takes priority over OLLAMA_LOCAL_HOST/OLLAMA_LOCAL_PORT.
    """
    import os

    ollama_host = os.environ.get(OLLAMA_HOST_ENV, "").strip()
    if ollama_host:
        # OLLAMA_HOST includes scheme+host[:port] (e.g. http://__VG_IPV4_b715750d32c1__:11434)
        base = ollama_host.rstrip("/")
        if not base.endswith("/v1"):
            base = base + "/v1"
        return base

    host = os.environ.get(OLLAMA_LOCAL_HOST_ENV, OLLAMA_LOCAL_DEFAULT_HOST)
    port = os.environ.get(OLLAMA_LOCAL_PORT_ENV, OLLAMA_LOCAL_DEFAULT_PORT)
    return f"http://{host}:{port}/v1"


def get_provider_base_url(provider):
    """Return the base URL for a provider, respecting env var overrides.

    Checks PROVIDER_BASE_URL_ENVS for an official SDK env var override.
    Falls back to BASE_URLS[provider] if no override is set.
    For 'meridian' and 'ollama-local', delegates to their dedicated functions.
    """
    import os

    if provider == "meridian":
        return get_meridian_base_url()
    if provider == "ollama-local":
        return get_ollama_local_base_url()

    env_var = PROVIDER_BASE_URL_ENVS.get(provider)
    if env_var:
        override = os.environ.get(env_var, "").strip()
        if override:
            return override.rstrip("/")

    return BASE_URLS.get(provider, get_ollama_local_base_url())


def is_meridian_configured():
    """Check if Meridian proxy is configured.

    Returns True if MERIDIAN_API_KEY or ANTHROPIC_BASE_URL is set.
    This is the canonical detection used by configure-smallcode.py
    and configure-opencode-voice.py.
    """
    import os

    return bool(
        os.environ.get("MERIDIAN_API_KEY", "").strip()
        or os.environ.get("ANTHROPIC_BASE_URL", "").strip()
    )
