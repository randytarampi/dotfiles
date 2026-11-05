"""Platform/owner qualification for the opt-in Xcode MCP bridge."""

import os
from pathlib import Path
import shutil
import subprocess  # nosec B404 - fixed local xcrun lookup.
import sys

GATE = "DOTFILES_RUN_XCODE_MCP_SETUP"


def enabled_entries(servers, environ=None):
    environ = os.environ if environ is None else environ
    return [
        item["template"]
        for item in servers
        if "template" in item
        and (not item.get("opt_in_gate") or environ.get(item["opt_in_gate"]) == "1")
    ]


def require_available(environ=None, platform=None, which=None, run=None):
    """Check installation, not Xcode app permissions or the MCP handshake."""
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    which = shutil.which if which is None else which
    run = subprocess.run if run is None else run
    if environ.get(GATE) != "1":
        raise ValueError("Xcode MCP requires explicit " + GATE + "=1")
    if platform != "darwin":
        raise ValueError("Xcode MCP is supported only on macOS")
    binary = which("xcrun")
    if not binary or not os.path.isabs(binary):
        raise ValueError("Xcode MCP requires installed xcrun")
    result = run(  # nosec B603 - resolved binary, fixed --find mcpbridge.
        [binary, "--find", "mcpbridge"],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    found = (result.stdout or "").strip()
    valid_path = bool(found) and Path(found).is_absolute()
    executable_bridge = (
        valid_path and Path(found).is_file() and os.access(found, os.X_OK)
    )
    if result.returncode != 0 or not executable_bridge:
        raise ValueError("Xcode mcpbridge unavailable or Xcode not selected")
    return binary
