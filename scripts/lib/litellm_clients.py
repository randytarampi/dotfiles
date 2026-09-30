"""Shared, opt-in client wiring for the local LiteLLM gateway."""

import os

CLIENT_GATES = {
    "openwebui": "DOTFILES_OPENWEBUI_USE_LITELLM",
    "opencode": "DOTFILES_OPENCODE_USE_LITELLM",
    "pi": "DOTFILES_PI_USE_LITELLM",
}


def client_uses_litellm(client, environ=None):
    environ = environ or os.environ
    return (
        environ.get("DOTFILES_RUN_LITELLM_SETUP", "0") == "1"
        and environ.get(CLIENT_GATES[client], "0") == "1"
    )


def litellm_endpoint(environ=None):
    environ = environ or os.environ
    return f"http://127.0.0.1:{environ.get('LITELLM_PORT', '4000')}/v1"
