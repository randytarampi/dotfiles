#!/usr/bin/env python3
"""Verify MCP registry template references match configs/mcp JSON files."""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY = REPO_ROOT / "configs/mcp/global-mcps.json"
MCP_DIR = REPO_ROOT / "configs/mcp"


def _templates(value):
    """Collect template names from the registry's known nested MCP shape."""
    if isinstance(value, dict):
        for item in value.get("mcp_servers", []):
            if isinstance(item, dict) and isinstance(item.get("template"), str):
                yield item["template"]
        for child in value.values():
            if isinstance(child, dict):
                yield from _templates(child)
            elif isinstance(child, list) and child is not value.get("mcp_servers"):
                for nested in child:
                    yield from _templates(nested)


def main():
    try:
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        referenced = {
            item
            for item in data.get("project_mcp_templates", [])
            if isinstance(item, str)
        }
        referenced.update(_templates(data.get("tools", {})))
        files = {
            path.stem
            for path in MCP_DIR.glob("*.json")
            if path.name != "global-mcps.json"
        }
    except (OSError, json.JSONDecodeError) as error:
        print(f"MCP parity error: {error}", file=sys.stderr)
        return 1
    missing = sorted(referenced - files)
    stray = sorted(files - referenced)
    for name in missing:
        print(f"missing MCP config: {name}.json", file=sys.stderr)
    for name in stray:
        print(f"unreferenced MCP config: {name}.json", file=sys.stderr)
    if missing or stray:
        return 1
    print(f"MCP parity OK: {len(files)} configs, {len(referenced)} templates")
    return 0


if __name__ == "__main__":
    sys.exit(main())
