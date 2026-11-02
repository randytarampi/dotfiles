#!/usr/bin/env python3
"""Opt-in tailnet-only Tailscale Serve route for authenticated OpenCode v2.

No device enrolment, public Funnel, Caddy rewriting, or automatic deployment.
"""

import argparse
import base64
import json
import os
import re
import shutil
import subprocess  # nosec B404 - fixed, allowlisted Tailscale CLI invocations.
import sys
import urllib.error
import urllib.request

GATE = "DOTFILES_RUN_TAILSCALE_ACCESS_SETUP"


def port(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]*", value):
        raise ValueError("port must be a canonical decimal integer")
    number = int(value)
    if not 1 <= number <= 65535:
        raise ValueError("port out of range")
    return number


def route_state(config, https_port, target):
    """Distinguish an absent, exactly managed or conflicting Serve port.

    A Tailscale Funnel permission on this port is always a conflict, even when
    the reverse proxy itself matches. Never convert public reachability into an
    apparently safe, tailnet-only configuration.
    """
    if not isinstance(config, dict):
        raise ValueError("invalid Tailscale Serve status")
    if set(config) - {"TCP", "Web", "AllowFunnel", "Services", "Foreground"}:
        raise ValueError("unrecognised Tailscale Serve configuration fields")
    if config.get("Services") or config.get("Foreground"):
        return "conflict"
    tcp = config.get("TCP", {})
    web = config.get("Web", {})
    funnel = config.get("AllowFunnel", {})
    if not all(isinstance(item, dict) for item in (tcp, web, funnel)):
        raise ValueError("invalid Tailscale Serve TCP/Web/Funnel status")
    selected = str(https_port)
    for host_port, allowed in funnel.items():
        if not isinstance(host_port, str):
            raise ValueError("invalid Tailscale Funnel host and port")
        if (
            host_port == selected or host_port.endswith(f":{selected}")
        ) and allowed is not False:
            return "conflict"
    entry = tcp.get(selected)
    hosts = [
        value
        for key, value in web.items()
        if isinstance(key, str) and key.endswith(f":{https_port}")
    ]
    if entry is None and not hosts:
        return "absent"
    if (
        not isinstance(entry, dict)
        or entry.get("HTTPS") is not True
        or len(hosts) != 1
        or not isinstance(hosts[0], dict)
    ):
        return "conflict"
    handlers = hosts[0].get("Handlers")
    if (
        not isinstance(handlers, dict)
        or set(handlers) != {"/"}
        or not isinstance(handlers["/"], dict)
    ):
        return "conflict"
    proxy = handlers["/"].get("Proxy")
    if isinstance(proxy, str) and proxy.rstrip("/") == target:
        return "ours"
    return "conflict"


def run_json(executable, *args):
    result = subprocess.run(  # nosec B603 - resolved Tailscale path; fixed arguments.
        [executable, *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return json.loads(result.stdout)


def verify_opencode(port_number, password):
    """Prove the local v2 consumer both rejects guests and accepts our secret.

    Never print password, Basic header or a captured HTTP exception body.
    """
    url = f"http://127.0.0.1:{port_number}/api/info"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=5):
            raise ValueError("OpenCode v2 API is accessible without authentication")
    except urllib.error.HTTPError as error:
        if error.code != 401:
            raise ValueError("OpenCode guest probe did not return HTTP 401") from None
    credentials = base64.b64encode(f"opencode:{password}".encode()).decode()
    request = urllib.request.Request(
        url, headers={"Authorization": f"Basic {credentials}"}
    )
    try:
        with opener.open(request, timeout=5) as response:
            if response.status != 200:
                raise ValueError("authenticated OpenCode v2 health check failed")
            info = json.load(response)
    except (urllib.error.HTTPError, urllib.error.URLError, UnicodeError):
        raise ValueError("authenticated OpenCode v2 health check failed") from None
    if not isinstance(info, dict) or not re.fullmatch(
        r"2\.[0-9]+\.[0-9]+", str(info.get("version", ""))
    ):
        raise ValueError("authenticated /api/info did not report stable OpenCode v2")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Publish only inside the tailnet"
    )
    parser.add_argument(
        "--https-port", default="8443", help="Dedicated tailnet HTTPS port"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview without mutation"
    )
    args = parser.parse_args(argv)
    try:
        https_port = port(args.https_port)
        backend_port = port(
            (os.environ.get("OPENCODE_SERVER_PORT") or "4096").strip() or "4096"
        )
        target = f"http://127.0.0.1:{backend_port}"
        executable = shutil.which("tailscale")
        if not executable:
            raise ValueError("tailscale CLI not installed")
        state = run_json(executable, "status", "--json")
        if not isinstance(state, dict) or state.get("BackendState") != "Running":
            raise ValueError("Tailscale is not running/enrolled")
        initial_config = run_json(executable, "serve", "status", "--json")
        verdict = route_state(initial_config, https_port, target)
        if verdict == "conflict":
            raise ValueError(
                "Serve HTTPS port is already owned or public; preserving it"
            )
        if not args.apply or args.dry_run:
            print(f"Tailnet-only route: {verdict}, HTTPS :{https_port} -> {target}")
            return 0
        if os.environ.get(GATE, "0") != "1":
            raise ValueError(f"explicit apply requires {GATE}=1")
        password = os.environ.get("OPENCODE_PASSWORD") or os.environ.get(
            "OPENCODE_SERVER_PASSWORD"
        )
        if not password:
            raise ValueError("OpenCode v2 server password must be configured")
        verify_opencode(backend_port, password)
        current_config = run_json(executable, "serve", "status", "--json")
        if current_config != initial_config:
            raise ValueError("Tailscale Serve config changed during validation; retry")
        if verdict == "ours":
            print("Tailnet-only OpenCode Serve route already configured and healthy")
            return 0
        # Tailscale CLI itself performs a fresh GetServeConfig/ETag SetServeConfig.
        # Avoid concurrent Serve owners while applying; local check is not a lock.
        subprocess.run(  # nosec B603 - resolved Tailscale path; fixed Serve arguments.
            [executable, "serve", "--bg", f"--https={https_port}", target],
            check=True,
            timeout=30,
        )
        if (
            route_state(
                run_json(executable, "serve", "status", "--json"), https_port, target
            )
            != "ours"
        ):
            raise ValueError("Serve succeeded but did not publish the private route")
        print(f"Tailnet-only OpenCode route configured at HTTPS :{https_port}")
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"Tailnet OpenCode setup refused: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
