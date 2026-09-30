#!/usr/bin/env python3
"""Read-only LiteLLM health and contract diagnostics."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))

from cli_helpers import add_common_args  # noqa: E402

GATE_ENV = "DOTFILES_RUN_LITELLM_SETUP"
LABEL = "com.litellm.proxy"


def _service_env(root: Path) -> dict[str, str]:
    values = {}
    path = root / "service.env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip("'\"")
    return values


def _base_url(env: dict[str, str]) -> str:
    override = os.environ.get("LITELLM_BASE_URL", "").strip()
    if override:
        return override.rstrip("/")
    return f"http://127.0.0.1:{os.environ.get('LITELLM_PORT', env.get('LITELLM_PORT', '4000'))}"


def _is_loopback_url(value: str) -> bool:
    parsed = urllib.parse.urlsplit(value)
    return (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        and not parsed.username
        and not parsed.password
    )


def _loaded() -> bool:
    if sys.platform != "darwin":
        try:
            result = subprocess.run(
                ["systemctl", "--user", "is-active", "litellm.service"],
                capture_output=True,
                text=True,
                check=False,
            )
            return result.returncode == 0
        except OSError:
            return False
    try:
        result = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0
    except OSError:
        return False


def _request(url: str, key: str = "") -> tuple[int, object | None, str]:
    request = urllib.request.Request(url, method="GET")
    if key:
        request.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = response.read().decode("utf-8", errors="replace")
            try:
                return response.status, json.loads(payload), ""
            except json.JSONDecodeError:
                return response.status, None, "invalid-json"
    except urllib.error.HTTPError as error:
        return error.code, None, "http-error"
    except (urllib.error.URLError, OSError) as error:
        return 0, None, type(error).__name__


def diagnose() -> tuple[int, str]:
    gate = os.environ.get(GATE_ENV, "0") == "1"
    root = Path(os.path.expanduser("~/.local/share/litellm"))
    env = _service_env(root)
    artifact = (root / "config.yaml").is_file() and (root / "service.env").is_file()
    if not gate:
        return 0, "LITELLM: GATE-OFF; artifact=%s" % artifact
    loaded = _loaded()
    base = _base_url(env)
    if not _is_loopback_url(base):
        return 1, f"LITELLM: PROXY-UNAVAILABLE; artifact={artifact}; loaded={loaded}"
    health = _request(f"{base}/health")
    readiness = _request(f"{base}/health/readiness")
    key = os.environ.get("LITELLM_MASTER_KEY", "").strip() or env.get(
        "LITELLM_MASTER_KEY", ""
    )
    if not key:
        return (
            1,
            f"LITELLM: MISSING-OR-INVALID-MASTER-KEY; artifact={artifact}; loaded={loaded}",
        )
    models = _request(f"{base}/v1/models", key)
    if health[0] == 0 or readiness[0] == 0:
        return 1, f"LITELLM: PROXY-UNAVAILABLE; artifact={artifact}; loaded={loaded}"
    if models[0] in {401, 403}:
        return (
            1,
            f"LITELLM: MISSING-OR-INVALID-MASTER-KEY; artifact={artifact}; loaded={loaded}",
        )
    if models[0] == 400:
        # DB-less LiteLLM rejects authenticated model requests with HTTP 400
        # (its known no_db_connection auth-backend rejection path). Real
        # HTTPError responses surface as (400, None, "http-error") from
        # _request(), so classify any 400 with healthy health endpoints as a
        # master-key/auth problem, not a provider failure.
        return (
            1,
            f"LITELLM: MISSING-OR-INVALID-MASTER-KEY; artifact={artifact}; loaded={loaded}",
        )
    if models[0] >= 500 or (isinstance(models[1], dict) and models[1].get("error")):
        return 1, f"LITELLM: PROVIDER-FAILURE; artifact={artifact}; loaded={loaded}"
    if 200 <= health[0] < 300 and 200 <= readiness[0] < 300 and 200 <= models[0] < 300:
        return 0, f"LITELLM: HEALTHY; artifact={artifact}; loaded={loaded}"
    return 1, f"LITELLM: PROXY-UNAVAILABLE; artifact={artifact}; loaded={loaded}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    add_common_args(parser)
    parser.parse_args()
    code, summary = diagnose()
    print(summary)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
