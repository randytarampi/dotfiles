#!/usr/bin/env python3
"""Read-only LiteLLM health and contract diagnostics."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import shutil
import socket
import subprocess  # nosec B404 - fixed-argument launchctl/systemctl probes only
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
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        return False
    try:
        addresses = {
            info[4][0]
            for info in socket.getaddrinfo(
                parsed.hostname, parsed.port or 80, type=socket.SOCK_STREAM
            )
        }
        return bool(addresses) and all(
            ipaddress.ip_address(address).is_loopback for address in addresses
        )
    except (OSError, ValueError):
        return False


def _loaded() -> bool:
    if sys.platform != "darwin":
        executable = shutil.which("systemctl")
        if not executable:
            return False
        try:
            result = (
                subprocess.run(  # nosec B603/B607 - fixed arguments, PATH-resolved tool
                    [executable, "--user", "is-active", "litellm.service"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            )
            return result.returncode == 0
        except OSError:
            return False
    executable = shutil.which("launchctl")
    if not executable:
        return False
    try:
        result = (
            subprocess.run(  # nosec B603/B607 - fixed arguments, PATH-resolved tool
                [executable, "print", f"gui/{os.getuid()}/{LABEL}"],
                capture_output=True,
                text=True,
                check=False,
            )
        )
        return result.returncode == 0
    except OSError:
        return False


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request(url: str, key: str = "") -> tuple[int, object | None, str]:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        return 0, None, "unsafe-scheme"
    request = urllib.request.Request(url, method="GET")
    if key:
        request.add_header("Authorization", f"Bearer {key}")
    try:
        opener = urllib.request.build_opener(_NoRedirectHandler())
        with opener.open(request, timeout=3) as response:
            payload = response.read().decode("utf-8", errors="replace")
            try:
                return response.status, json.loads(payload), ""
            except json.JSONDecodeError:
                return response.status, None, "invalid-json"
    except urllib.error.HTTPError as error:
        return error.code, None, "http-error"
    # URLError subclasses OSError; one clause covers both (qlty S5713).
    except OSError as error:
        return 0, None, type(error).__name__


def diagnose(master_key: str | None = None) -> tuple[int, str]:
    gate = os.environ.get(GATE_ENV, "0") == "1"
    root = Path(os.path.expanduser("~/.local/share/litellm"))
    env = _service_env(root)
    artifact = (root / "config.yaml").is_file() and (root / "service.env").is_file()
    if not gate:
        return 0, "LITELLM: GATE-OFF; artifact=%s" % artifact
    loaded = _loaded()
    base = _base_url(env)
    if not loaded:
        return 1, f"LITELLM: PROXY-UNAVAILABLE; artifact={artifact}; loaded={loaded}"
    if not _is_loopback_url(base):
        return 1, f"LITELLM: PROXY-UNAVAILABLE; artifact={artifact}; loaded={loaded}"
    key = (
        (master_key or "").strip()
        or os.environ.get("LITELLM_MASTER_KEY", "").strip()
        or env.get("LITELLM_MASTER_KEY", "")
    )
    liveliness = _request(f"{base}/health/liveliness")
    # Litellm's liveliness endpoint returns the JSON string "I'm alive!",
    # never a dict; accept any JSON body with a 2xx status.
    if not (200 <= liveliness[0] < 300 and liveliness[1] is not None):
        return 1, f"LITELLM: PROXY-UNAVAILABLE; artifact={artifact}; loaded={loaded}"
    if not key:
        return (
            1,
            f"LITELLM: MISSING-OR-INVALID-MASTER-KEY; artifact={artifact}; loaded={loaded}",
        )
    readiness = _request(f"{base}/health/readiness", key)
    models = _request(f"{base}/v1/models", key)
    if any(
        status == 200 and parsed is None for status, parsed, _ in (readiness, models)
    ):
        return (
            1,
            f"LITELLM: PROXY-UNAVAILABLE; reason=invalid-json; artifact={artifact}; loaded={loaded}",
        )
    models_shape = isinstance(models[1], dict) and isinstance(
        models[1].get("data"), list
    )
    readiness_shape = isinstance(readiness[1], dict)
    if liveliness[0] == 0 or readiness[0] == 0 or not readiness_shape:
        return 1, f"LITELLM: PROXY-UNAVAILABLE; artifact={artifact}; loaded={loaded}"
    if models[0] in {401, 403}:
        return (
            1,
            f"LITELLM: MISSING-OR-INVALID-MASTER-KEY; artifact={artifact}; loaded={loaded}",
        )
    if models[0] == 400:
        # Retain this branch for legacy DB-less deployments. New gate-on
        # deployments require DATABASE_URL, so this is not an expected path.
        # DB-less LiteLLM rejects authenticated model requests with HTTP 400
        # (its known no_db_connection auth-backend rejection path). The
        # auth-backend 400 path is likewise a master-key/auth problem. Real
        # HTTPError responses surface as (400, None, "http-error") from
        # _request(), so classify any 400 with healthy health endpoints as a
        # master-key/auth problem, not a provider failure.
        return (
            1,
            f"LITELLM: MISSING-OR-INVALID-MASTER-KEY; artifact={artifact}; loaded={loaded}",
        )
    if models[0] >= 500 or (isinstance(models[1], dict) and models[1].get("error")):
        return 1, f"LITELLM: PROVIDER-FAILURE; artifact={artifact}; loaded={loaded}"
    if (
        200 <= liveliness[0] < 300
        and 200 <= readiness[0] < 300
        and 200 <= models[0] < 300
        and models_shape
    ):
        return 0, f"LITELLM: HEALTHY; artifact={artifact}; loaded={loaded}"
    return 1, f"LITELLM: PROXY-UNAVAILABLE; artifact={artifact}; loaded={loaded}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    add_common_args(parser)
    parser.add_argument(
        "--master-key", help="LiteLLM master key for authenticated probes"
    )
    args = parser.parse_args()
    code, summary = diagnose(args.master_key)
    print(summary)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
