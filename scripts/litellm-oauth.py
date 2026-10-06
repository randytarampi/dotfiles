#!/usr/bin/env python3
"""Interactively bootstrap a LiteLLM OAuth provider and verify inference."""

import argparse
import os
import subprocess
import sys
from pathlib import Path

PYTHON = "/Users/randytarampi/.local/share/litellm/venv/bin/python"


def cache_path(provider):
    if provider == "github_copilot":
        directory = Path(
            os.environ.get(
                "GITHUB_COPILOT_TOKEN_DIR", "~/.config/litellm/github_copilot"
            )
        ).expanduser()
        return directory / os.environ.get("GITHUB_COPILOT_API_KEY_FILE", "api-key.json")
    directory = Path(
        os.environ.get("CHATGPT_TOKEN_DIR", "~/.config/litellm/chatgpt")
    ).expanduser()
    return directory / os.environ.get("CHATGPT_AUTH_FILE", "auth.json")


PROVIDERS = ("github_copilot", "chatgpt")
MODELS = {"github_copilot": "github_copilot/gpt-4o", "chatgpt": "chatgpt/gpt-5.2"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True, choices=PROVIDERS)
    args = parser.parse_args(argv)
    if not sys.stdout.isatty() or not sys.stdin.isatty():
        print(
            "Refusing OAuth bootstrap without an interactive terminal.", file=sys.stderr
        )
        return 2
    provider = args.provider
    print(
        f"Provider: {provider}\nCache: {cache_path(provider)}\n"
        "WARNING: a device-code URL and authorization code may be shown; complete login only if you initiated it.",
        flush=True,
    )
    code = (
        "import sys; "
        f"from litellm.llms.{provider}.authenticator import Authenticator; "
        "auth = Authenticator(); "
        "getattr(auth, 'get_api_key', auth.get_access_token)()"
    )
    result = subprocess.run([PYTHON, "-c", code], check=False)
    if result.returncode:
        print(f"OAuth login failed (exit {result.returncode}).", file=sys.stderr)
        return 1
    model = MODELS[provider]
    verification = (
        "import litellm; "
        f"response = litellm.completion(model={model!r}, messages=[{{'role':'user','content':'Reply with one character.'}}], max_tokens=1); "
        "print('Verification request succeeded:', bool(response))"
    )
    result = subprocess.run([PYTHON, "-c", verification], check=False)
    print(
        "Verification request succeeded."
        if result.returncode == 0
        else "Verification request failed."
    )
    return 0 if result.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
