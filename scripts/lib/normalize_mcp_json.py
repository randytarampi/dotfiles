#!/usr/bin/env python3
"""Normalize opencode.json MCP config after 'codegraph install' writes it.

The codegraph CLI (1.6.2) emits an undocumented nested snippet shape:

    "mcp": { "servers": { "codegraph": { ... } } }

opencode v1 expects a flat top-level "mcp" map with server names as keys
(https://opencode.ai/docs/mcp-servers/); the doctor check in
scripts/verify-config.py likewise requires a flat "mcp.codegraph" entry.

Promotes nested "mcp.servers" entries to flat "mcp" keys, keeping only
documented opencode v1 fields, converting "disabled" to inverted "enabled"
and dropping undocumented fields ("disabled", "codemode"). Registry-authored
flat entries take precedence over promoted entries. The "servers" wrapper is
removed entirely. No-op when no wrapper is present (idempotent).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

OPENCODE_CONFIG_PATH = os.path.expanduser("~/.config/opencode/opencode.json")

# Documented opencode v1 MCP entry fields
# (https://opencode.ai/docs/mcp-servers/): local — type, command, cwd,
# environment, enabled, timeout; remote adds url, headers, oauth.
ALLOWED_FIELDS = (
    "type",
    "command",
    "cwd",
    "environment",
    "enabled",
    "timeout",
    "url",
    "headers",
    "oauth",
)


def normalize(path: str = OPENCODE_CONFIG_PATH) -> bool:
    """Promote nested mcp.servers entries to flat mcp keys. True if rewritten."""
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as fh:
            config = json.load(fh)
    except (OSError, ValueError):
        return False

    if not isinstance(config, dict):
        return False

    mcp = config.get("mcp")
    if not isinstance(mcp, dict) or not isinstance(mcp.get("servers"), dict):
        return False

    for name, entry in mcp.pop("servers").items():
        if not isinstance(entry, dict) or name in mcp:
            continue  # flat registry-authored entry wins
        if entry.get("type") not in ("local", "remote"):
            continue
        disabled = entry.get("disabled", False)
        cleaned = {k: v for k, v in entry.items() if k in ALLOWED_FIELDS}
        cleaned["enabled"] = False if disabled else cleaned.get("enabled", True)
        mcp[name] = cleaned

    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=os.path.dirname(os.path.abspath(path)),
            delete=False,
        ) as fh:
            temporary_path = fh.name
            json.dump(config, fh, indent=2)
            fh.write("\n")
        os.replace(temporary_path, path)
    except (OSError, TypeError):
        return False
    finally:
        if temporary_path is not None and os.path.exists(temporary_path):
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
    return True


if __name__ == "__main__":
    path_arg = sys.argv[1] if len(sys.argv) > 1 else OPENCODE_CONFIG_PATH
    normalize(path_arg)
