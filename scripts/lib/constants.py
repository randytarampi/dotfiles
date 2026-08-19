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

import logging

_log = logging.getLogger(__name__)

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
OLLAMA_CLOUD_PROXY_ENV = "DOTFILES_USE_OLLAMA_CLOUD_PROXY"

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
    For 'meridian' and 'ollama', delegates to their dedicated functions.
    """
    import os

    if provider == "meridian":
        return get_meridian_base_url()
    if provider == "ollama":
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


def should_use_ollama_cloud_proxy():
    """Check if Ollama cloud proxy routing is enabled.

    Returns True unless DOTFILES_USE_OLLAMA_CLOUD_PROXY is explicitly set
    to false/0.
    """
    import os

    value = os.environ.get(OLLAMA_CLOUD_PROXY_ENV, "").strip().lower()
    return value not in {"0", "false"}


def check_ollama_daemon():
    """Check whether local Ollama is running and cloud-signed-in.

    Returns:
      (is_running, can_proxy_cloud)
    """
    if not should_use_ollama_cloud_proxy():
        _log.info("%s disabled; skipping Ollama daemon check", OLLAMA_CLOUD_PROXY_ENV)
        return (False, False)

    try:
        import urllib.error
        import urllib.parse
        import urllib.request

        local_base = get_ollama_local_base_url()
        parsed = urllib.parse.urlsplit(local_base)
        daemon_path = parsed.path[:-3] if parsed.path.endswith("/v1") else parsed.path
        daemon_base = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, daemon_path, "", "")
        )
        tags_url = f"{daemon_base}/api/tags"

        _log.info("Checking Ollama daemon at %s", daemon_base)

        tags_request = urllib.request.Request(tags_url, method="GET")
        try:
            with urllib.request.urlopen(tags_request, timeout=3):
                pass
        except urllib.error.HTTPError as err:
            _log.info("Ollama daemon responded to /api/tags with HTTP %s", err.code)

        _log.info("Ollama daemon is running; checking cloud proxy sign-in")

        can_proxy_cloud = _check_ollama_cloud_signin(daemon_base)
        _log.info("Ollama cloud proxy available: %s", can_proxy_cloud)
        return (True, can_proxy_cloud)
    except Exception as err:
        _log.warning("Failed to check Ollama daemon: %s", err)
        return (False, False)


def _check_ollama_cloud_signin(daemon_base):
    """Check whether the local Ollama daemon is signed in for cloud proxy.

    Tries POST /api/me first (required by Ollama >= 0.30), then falls back
    to checking for the signin keypair file at ~/.ollama/id_ed25519.

    Returns:
      True if the daemon can proxy cloud model requests.
    """
    import urllib.error
    import urllib.request

    # POST /api/me — the endpoint that Ollama >= 0.30 expects
    me_url = f"{daemon_base}/api/me"
    me_request = urllib.request.Request(me_url, data=b"", method="POST")
    me_request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(me_request, timeout=3) as response:
            if response.status == 200:
                _log.info("Ollama daemon /api/me confirmed cloud sign-in")
                return True
    except urllib.error.HTTPError as err:
        if err.code == 401:
            _log.info("Ollama daemon /api/me returned 401; not signed in for cloud")
            return False
        if err.code == 405:
            # Older Ollama versions may not support /api/me at all;
            # fall through to file-based check below.
            _log.debug("Ollama daemon /api/me returned 405; trying file-based check")
        else:
            _log.warning(
                "Ollama daemon /api/me returned HTTP %s; trying file-based check",
                err.code,
            )
    except Exception as err:
        _log.debug(
            "Ollama daemon /api/me request failed: %s; trying file-based check", err
        )

    # Fallback: check for the signin keypair file.
    # After `ollama signin`, credentials are stored at ~/.ollama/id_ed25519.
    import os

    home = os.path.expanduser("~")
    key_file = os.path.join(home, ".ollama", "id_ed25519")
    if os.path.isfile(key_file):
        _log.info("Ollama signin key found at %s; assuming cloud-capable", key_file)
        return True

    _log.info("No Ollama cloud sign-in detected (no key file at %s)", key_file)
    return False


def get_ollama_base_url():
    """Return the preferred Ollama base URL for unified routing.

    Uses the local daemon when it can proxy cloud models; otherwise falls
    back to the direct Ollama Cloud base URL.
    """
    is_running, can_proxy_cloud = check_ollama_daemon()
    if is_running and can_proxy_cloud:
        return get_ollama_local_base_url()
    return get_provider_base_url("ollama-cloud")
