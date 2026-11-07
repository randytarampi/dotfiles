#!/usr/bin/env python3
"""Read-only, optional loopback diagnostics for existing local inference services.

Service reachability is not a model, account, or consumer-acceptance claim.
"""

import argparse
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from local_engines import LOCAL_ENGINES, engine_gate_active  # noqa: E402


def loopback_origin(value):
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in ("localhost", "127.0.0.1", "::1")
            or not parsed.port
            or parsed.username
            or parsed.password
            or parsed.fragment
            or parsed.query
        ):
            return None
        if not 1 <= parsed.port <= 65535:
            return None
        host = "[::1]" if parsed.hostname == "::1" else parsed.hostname
        return f"http://{host}:{parsed.port}"
    except ValueError:
        return None


def probe_http(url, *, opener=urlopen):
    """Read only one anonymous local health/catalogue route; never send keys."""
    origin = loopback_origin(url)
    if not origin:
        return "REFUSED_NON_LOOPBACK"
    try:
        response = opener(Request(url, method="GET"), timeout=1)
        with response:
            code = response.status
            response.read(1024)
        if code == 200:
            return "REACHABLE_NOT_INFERENCE_QUALIFIED"
        return "UNKNOWN_HTTP_STATUS"
    except HTTPError as exc:
        if exc.code in (401, 403):
            return "REACHABLE_AUTH_REQUIRED"
        return "UNKNOWN_HTTP_STATUS"
    except (URLError, TimeoutError, OSError):
        return "UNAVAILABLE"


def targets(environ=None):
    """Use the canonical local engine registry rather than a second model list."""
    env = environ if environ is not None else os.environ
    result = []
    for name, spec in LOCAL_ENGINES.items():
        gate = spec.get("gate_env")
        enabled = gate is None or env.get(gate) == "1"
        try:
            base = spec["base_url"]()
        except (OSError, ValueError):
            base = ""
        origin = loopback_origin(base)
        result.append({
            "service": name,
            "configured": enabled,
            "origin": origin or "UNKNOWN_OR_NOT_LOOPBACK",
            "health_path": "/api/tags" if name == "ollama" else "/health",
            "model_ready": "UNKNOWN",
            "consumer_ready": "UNKNOWN",
        })
    if (
        env.get("DOTFILES_RUN_LITELLM_SETUP") == "1"
        and env.get("DOTFILES_USE_LITELLM_PROXY") == "1"
    ):
        port = env.get("LITELLM_PORT", "4000")
        origin = loopback_origin(f"http://127.0.0.1:{port}")
        result.append({
            "service": "litellm",
            "configured": True,
            "origin": origin or "UNKNOWN_OR_NOT_LOOPBACK",
            "health_path": "/v1/models",
            "model_ready": "UNKNOWN",
            "consumer_ready": "UNKNOWN",
        })
    return result


def collect(*, probe=False, environ=None, opener=urlopen):
    report = []
    for item in targets(environ=environ):
        entry = dict(item)
        entry["observation"] = "NOT_PROBED"
        if probe and entry["configured"]:
            origin = entry["origin"]
            if origin == "UNKNOWN_OR_NOT_LOOPBACK":
                entry["observation"] = "REFUSED_NON_LOOPBACK"
            else:
                entry["observation"] = probe_http(
                    origin + entry["health_path"], opener=opener
                )
        elif not entry["configured"]:
            entry["observation"] = "GATE_DISABLED"
        report.append(entry)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true", help="opt-in anonymous local GET")
    parser.add_argument("--strict", action="store_true", help="exit nonzero on unknown enabled route")
    args = parser.parse_args()
    report = collect(probe=args.probe)
    print(json.dumps({"checks": report}, indent=2, sort_keys=True))
    if args.strict and any(
        r["configured"]
        and r["observation"] != "REACHABLE_NOT_INFERENCE_QUALIFIED"
        for r in report
    ):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
