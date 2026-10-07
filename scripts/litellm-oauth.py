#!/usr/bin/env python3
"""Interactively bootstrap a LiteLLM OAuth provider and verify inference."""

import argparse
import os
import subprocess  # nosec B404 - used only for the managed LiteLLM interpreter.
import sys
import json
import tempfile
from pathlib import Path
import stat
import time

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


def classify_probe_failure(returncode, output):
    """Classify known subscription denials and retryable transport artifacts.

    Keep transient markers narrow: recognized API errors, network failures,
    rate limits, and Cloudflare challenge evidence only.
    """
    text = str(output or "").lower()
    if "is not supported when using codex with a chatgpt account" in text:
        return "entitlement"
    if "unknown items in responses api response" in text:
        return "artifact"
    if "permissiondeniederror" in text and any(
        marker in text
        for marker in ("__cf_chl", "cf-chl", "cloudflare", "challenge-platform")
    ):
        return "artifact"
    if any(
        marker in text
        for marker in ("ratelimiterror", "429", "timeout", "connectionerror")
    ):
        return "artifact"
    if any(marker in text for marker in ("litellm", "openai")) and any(
        marker in text
        for marker in ("<html", "<!doctype html", "challenge-platform", "cf-chl")
    ):
        return "artifact"
    return "unknown"


def verify_chatgpt_openai_models(
    python=None,
    refs=None,
    verified_path=None,
    deferred_path=None,
    run=None,
    sleep=time.sleep,
):
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
    deferred_target = Path(
        deferred_path
        or Path.home() / ".local/share/litellm/chatgpt_deferred_models.json"
    )
    runner = run or subprocess.run
    for path in (target, deferred_target):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.chmod(0o700)
    verified = []
    deferred = {"entitlement": [], "artifact": []}
    unknown = []
    for model_id in model_ids:
        code = (
            "import litellm; "
            f"response = litellm.completion(model={'chatgpt/' + model_id!r}, "
            "messages=[{'role':'user','content':'Reply with one character.'}], max_tokens=16); "
            "assert response"
        )
        failure_class = None
        for attempt in range(3):
            try:
                result = runner(
                    [str(python), "-c", code],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    verified.append(model_id)
                    print(f"openai/{model_id}: VERIFIED — inference succeeded")
                    break
                output = "\n".join((result.stdout or "", result.stderr or ""))
                failure_class = classify_probe_failure(result.returncode, output)
            except OSError as error:
                failure_class = "unknown"
                output = str(error)
            if failure_class == "artifact" and attempt < 2:
                sleep(10)
                continue
            if failure_class in deferred:
                deferred[failure_class].append(model_id)
                print(f"openai/{model_id}: DEFERRED ({failure_class})")
            else:
                unknown.append(model_id)
                print(f"openai/{model_id}: FAILED — unknown probe failure")
            break
        else:
            # A successful final attempt has already recorded the model.
            pass
    if unknown:
        print(
            "Unknown probe failures; preserving the existing verified-model file: "
            + ", ".join(unknown)
        )
        return verified, False

    try:
        existing = set(json.loads(target.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        existing = set()
    dropped = sorted(existing - set(verified))
    if dropped:
        print(
            "WARNING: verified model coverage shrank; dropped ids: "
            + ", ".join(dropped)
        )

    def atomic_json(path, payload):
        fd, temp_path = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(payload, output)
                output.write("\n")
            os.replace(temp_path, path)
            os.chmod(path, 0o600)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    atomic_json(target, verified)
    atomic_json(
        deferred_target, {key: sorted(value) for key, value in deferred.items()}
    )
    summary = [
        "ChatGPT subscription probe summary:",
        "  Verified (routed): " + (", ".join(verified) or "none"),
        "  Deferred entitlement: "
        + (", ".join(sorted(deferred["entitlement"])) or "none"),
        "  Deferred artifact: " + (", ".join(sorted(deferred["artifact"])) or "none"),
        "  Rerun `make deploy` to regenerate and clear verified refs from preflight coverage.",
    ]
    if not verified:
        summary.insert(
            0,
            "WARNING: no models verified via subscription transport; all refs remain deferred (honest red in preflight).",
        )
    print("\n".join(summary))
    return verified, True


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
    result = subprocess.run(  # nosec B603 - managed interpreter and internally generated code.
        [str(python), "-c", code], check=False
    )
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
        f"response = litellm.completion(model={model!r}, messages=[{{'role':'user','content':'Reply with one character.'}}], max_tokens=16); "
        "print('Verification request succeeded:', bool(response))"
    )
    result = subprocess.run(  # nosec B603 - managed interpreter and internally generated verification.
        [str(python), "-c", verification], check=False, capture_output=True, text=True
    )
    main_probe_unknown = False
    if provider == "chatgpt" and result.returncode != 0:
        try:
            probe_output = "\n".join((result.stdout or "", result.stderr or ""))
            probe_class = classify_probe_failure(result.returncode, probe_output)
        except AttributeError:
            probe_class = "unknown"
        if probe_class == "entitlement":
            print(
                f"WARNING: main probe model {model} is unavailable via ChatGPT subscription; continuing with per-model verification."
            )
            result.returncode = 0
        else:
            main_probe_unknown = True
    print(
        "Verification request succeeded."
        if result.returncode == 0
        else "Verification request failed."
    )
    if provider == "chatgpt":
        _, models_verified = verify_chatgpt_openai_models(python)
        return 0 if models_verified and not main_probe_unknown else 1
    return 0 if result.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
