#!/usr/bin/env python3
"""Normalize opencode.json MCP config after 'codegraph install' writes it.

The codegraph CLI (1.6.2) emits an undocumented nested snippet shape:

    "mcp": { "servers": { "codegraph": { ... } } }

Direction follows the installed OpenCode host (see scripts/lib/opencode_host.py):

- OpenCode v2 (or not installed): the managed native shape is the nested
  "mcp.servers" wrapper matching the v2 config docs and the canonical
  emitter. Flat legacy "mcp" entries are promoted into the wrapper with
  "enabled" inverted to "disabled"; field whitelisting is dropped so v2
  fields (timeout {catalog,execution}, snake_case OAuth members, codemode,
  ...) survive untouched. A conflicting flat entry loses to the nested one;
  a malformed nested entry never overrides its flat twin.
- OpenCode v1 (or an unprobeable host): the legacy v1 behaviour applies —
  promote nested entries to flat keys keeping only documented v1 fields,
  convert "disabled" to inverted "enabled", drop undocumented fields, and
  remove the wrapper, so the v1 binary (which does not read mcp.servers)
  keeps codegraph registered.

Both directions are idempotent and atomic; malformed or non-dict inputs are
left untouched and return False.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

try:
    import opencode_host  # type: ignore[import-not-found] -- scripts/lib on sys.path
except ImportError:  # pragma: no cover - direct execution puts scripts/lib on sys.path
    if "/scripts/lib" in sys.path[0] or __package__:
        raise
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import opencode_host  # type: ignore[import-not-found]

OPENCODE_CONFIG_PATH = os.path.expanduser("~/.config/opencode/opencode.json")

# Documented opencode v1 MCP entry fields
# (https://opencode.ai/docs/mcp-servers/): local — type, command, cwd,
# environment, enabled, timeout; remote adds url, headers, oauth.
V1_ALLOWED_FIELDS = (
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
    """Align the MCP block with the installed host's expected shape.
    True if rewritten."""
    path = os.path.realpath(path)
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
    if not isinstance(mcp, dict):
        return False

    host_state, host_major = opencode_host.resolve_opencode_host()
    if host_state == "absent" or host_major == 2:
        normalized = _normalize_native(mcp)
    else:
        normalized = _normalize_v1(mcp)
    if normalized is not True:
        return False

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
    return normalized


def _normalize_native(mcp):
    """Ensure the native v2 mcp.servers wrapper survives codegraph writes.

    Flat legacy entries are promoted into the wrapper with "enabled"
    inverted to "disabled" (the last registry-authored v1 shape). Fields
    are preserved verbatim; nested entries win conflicts; a malformed
    flat entry refuses the rewrite (returns False without mutation).
    Returns True when the block changed.
    """
    servers = mcp.get("servers")
    if not isinstance(servers, dict):
        servers = {}
    changed = False
    for name, entry in list(mcp.items()):
        if name == "servers" or not isinstance(entry, dict):
            continue
        nested = servers.get(name)
        if isinstance(nested, dict):
            continue  # nested registry/emitter entry wins
        if entry.get("type") not in ("local", "remote"):
            return False
        cleaned = dict(entry)
        enabled = cleaned.pop("enabled", None)
        cleaned["disabled"] = enabled is False
        servers[name] = cleaned
        del mcp[name]
        changed = True
    if servers and "servers" not in mcp:
        mcp["servers"] = servers
        changed = True
    return True if changed else False


def _normalize_v1(mcp):
    """Legacy v1 direction: promote nested to flat, keep documented fields,
    invert "disabled" to "enabled", drop undocumented fields, remove the
    wrapper. None means "do not rewrite"."""
    if not isinstance(mcp.get("servers"), dict):
        return None
    servers = mcp["servers"]
    promoted = {}
    for name, entry in servers.items():
        if name in mcp:
            continue  # flat registry-authored entry wins
        if not isinstance(entry, dict) or entry.get("type") not in ("local", "remote"):
            return None
        disabled = entry.get("disabled", False)
        cleaned = {k: v for k, v in entry.items() if k in V1_ALLOWED_FIELDS}
        cleaned["enabled"] = False if disabled else cleaned.get("enabled", True)
        promoted[name] = cleaned

    mcp.pop("servers")
    mcp.update(promoted)
    return True


if __name__ == "__main__":
    path_arg = sys.argv[1] if len(sys.argv) > 1 else OPENCODE_CONFIG_PATH
    normalize(path_arg)
