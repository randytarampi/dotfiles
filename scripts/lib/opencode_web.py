"""Fail-closed OpenCode v2 web server probe."""

import base64
import json
import os
import re
import sys
from typing import Optional
import urllib.error
import urllib.request

_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


def server_password_env_chain():
    """Return the set password variable name in OpenCode precedence order."""
    for name in ("OPENCODE_PASSWORD", "OPENCODE_SERVER_PASSWORD"):
        if os.environ.get(name):
            return name
    return None


def request_server_info(
    base_url,
    password,
    username="opencode",
    timeout=3,
    expected_version: Optional[str] = None,
):
    """Fetch authenticated v2 ServerInfo; never include credentials in errors."""
    request = urllib.request.Request(base_url.rstrip("/") + "/api/info")
    token = base64.b64encode((username + ":" + password).encode()).decode()
    request.add_header("Authorization", "Basic " + token)
    try:
        with urllib.request.urlopen(  # nosec B310 - fixed http://127.0.0.1 scheme built by trusted callers.
            request, timeout=timeout
        ) as response:
            if response.status != 200:
                return "fail", "http-status:{}".format(response.status)
            content_type = (
                response.headers.get("Content-Type", "")
                .split(";", 1)[0]
                .strip()
                .lower()
            )
            if content_type != "application/json":
                return "fail", "non-json"
            try:
                info = json.loads(response.read().decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return "fail", "non-json"
    except urllib.error.HTTPError as error:
        return "fail", "http-status:{}".format(error.code)
    except Exception:
        return "fail", "request-failed"
    if not isinstance(info, dict) or not isinstance(info.get("version"), str):
        return "fail", "invalid-server-info"
    version = info["version"]
    if not _VERSION.match(version):
        return "fail", "invalid-version"
    if expected_version and version != expected_version:
        return "fail", "version-mismatch:{}".format(version)
    return "ok", info


if __name__ == "__main__":
    port, password = sys.argv[1:3]
    result, detail = request_server_info("http://127.0.0.1:{}".format(port), password)
    if result != "ok":
        sys.exit(1)
