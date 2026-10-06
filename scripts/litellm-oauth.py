#!/usr/bin/env python3
"""Interactively bootstrap a LiteLLM OAuth provider and verify inference."""

import argparse
import os
import subprocess
import sys
import json
import tempfile
from pathlib import Path
import stat

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from env import load_env


def litellm_python():
    return Path("~/.local/share/litellm/venv/bin/python").expanduser()


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


def verify_chatgpt_openai_models(python=None, refs=None, verified_path=None, run=None):
    """Verify each registry OpenAI ID interactively and atomically persist successes."""
    if refs is None:
        sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
        import litellm_config

        refs = litellm_config._registry_model_refs()
    model_ids = sorted(
        {
            ref.split("/", 1)[1]
            for ref in refs
            if isinstance(ref, str) and ref.startswith("openai/")
        }
    )
    python = Path(python or litellm_python())
    target = Path(
        verified_path
        or Path.home() / ".local/share/litellm/chatgpt_verified_models.json"
    )
    runner = run or subprocess.run
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.parent.chmod(0o700)
    verified = []
    results = []
    for model_id in model_ids:
        code = (
            "import litellm; "
            f"response = litellm.completion(model={'chatgpt/' + model_id!r}, "
            "messages=[{'role':'user','content':'Reply with one character.'}], max_tokens=1); "
            "assert response"
        )
        try:
            result = runner([str(python), "-c", code], check=False)
            passed = result.returncode == 0
            reason = (
                "inference succeeded"
                if passed
                else f"request failed (exit {result.returncode})"
            )
        except OSError:
            passed = False
            reason = "request could not be started"
        print(f"openai/{model_id}: {'VERIFIED' if passed else 'FAILED'} — {reason}")
        results.append(passed)
        if passed:
            verified.append(model_id)
    fd, temp_path = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(verified, output)
            output.write("\n")
        os.replace(temp_path, target)
        os.chmod(target, 0o600)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    return verified, all(results)


def main(argv=None):
    os.umask(0o077)
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True, choices=PROVIDERS)
    args = parser.parse_args(argv)
    python = litellm_python()
    if not python.is_file():
        print(
            "LiteLLM virtualenv Python is unavailable; run LiteLLM setup first.",
            file=sys.stderr,
        )
        return 2
    if not sys.stdout.isatty() or not sys.stdin.isatty():
        print(
            "Refusing OAuth bootstrap without an interactive terminal.", file=sys.stderr
        )
        return 2
    provider = args.provider
    cache = cache_path(provider)
    cache.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    cache.parent.chmod(0o700)
    print(
        f"Provider: {provider}\nCache: {cache}\n"
        "WARNING: a device-code URL and authorization code may be shown; complete login only if you initiated it.",
        flush=True,
    )
    code = (
        "import sys; "
        f"from litellm.llms.{provider}.authenticator import Authenticator; "
        "auth = Authenticator(); "
        "getattr(auth, 'get_api_key', auth.get_access_token)()"
    )
    result = subprocess.run([str(python), "-c", code], check=False)
    if result.returncode:
        print(f"OAuth login failed (exit {result.returncode}).", file=sys.stderr)
        return 1
    hardened = False
    for path in (cache.parent, cache):
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
            expected = 0o700 if path.is_dir() else 0o600
            if mode & 0o077 or (path.is_dir() and mode != expected):
                path.chmod(expected)
                hardened = True
        except OSError:
            pass
    if hardened:
        print("Hardened OAuth cache permissions (directory 0700, file 0600).")
    model = MODELS[provider]
    verification = (
        "import litellm; "
        f"response = litellm.completion(model={model!r}, messages=[{{'role':'user','content':'Reply with one character.'}}], max_tokens=1); "
        "print('Verification request succeeded:', bool(response))"
    )
    result = subprocess.run([str(python), "-c", verification], check=False)
    print(
        "Verification request succeeded."
        if result.returncode == 0
        else "Verification request failed."
    )
    if provider == "chatgpt":
        _, models_verified = verify_chatgpt_openai_models(python)
        return 0 if result.returncode == 0 and models_verified else 1
    return 0 if result.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
