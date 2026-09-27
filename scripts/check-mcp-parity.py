#!/usr/bin/env python3
"""Verify MCP registry template references match configs/mcp JSON files."""

import json
import sys
import argparse
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
LIB_DIR = SCRIPT_DIR / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import logger  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY = REPO_ROOT / "configs/mcp/global-mcps.json"
MCP_DIR = REPO_ROOT / "configs/mcp"
TEMPLATES_DIR = MCP_DIR / "templates"


def _server_templates(value):
    """Collect template names from one registry object."""
    if not isinstance(value, dict):
        return
    for item in value.get("mcp_servers", []):
        if isinstance(item, dict) and isinstance(item.get("template"), str):
            yield item["template"]


def _walk_templates(value):
    """Walk nested registry values without revisiting the server list."""
    if isinstance(value, dict):
        yield from _server_templates(value)
        for key, child in value.items():
            if key != "mcp_servers":
                yield from _walk_templates(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_templates(child)


def _json_stems(directory):
    return {path.stem for path in directory.glob("*.json")}


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Verify MCP config and template directories have equal coverage."
    )
    parser.add_argument("--registry", type=Path, default=REGISTRY)
    parser.add_argument("--mcp-dir", type=Path, default=MCP_DIR)
    parser.add_argument("--templates-dir", type=Path, default=TEMPLATES_DIR)
    return parser.parse_args()


def _report_difference(names, message):
    for name in sorted(names):
        logger.error("%s: %s.json", message, name)


def main():
    args = _parse_args()
    try:
        data = json.loads(args.registry.read_text(encoding="utf-8"))
        referenced = {
            item
            for item in data.get("project_mcp_templates", [])
            if isinstance(item, str)
        }
        referenced.update(_walk_templates(data.get("tools", {})))
        configs = _json_stems(args.mcp_dir) - {args.registry.stem}
        templates = _json_stems(args.templates_dir)
    except (OSError, json.JSONDecodeError) as error:
        logger.error("MCP parity error: %s", error)
        return 1
    missing_configs = referenced - configs
    unreferenced_configs = configs - referenced
    missing_configs_for_templates = templates - configs
    stray_templates = configs - templates
    _report_difference(missing_configs, "missing MCP config")
    _report_difference(unreferenced_configs, "unreferenced MCP config")
    _report_difference(missing_configs_for_templates, "missing MCP config for template")
    _report_difference(stray_templates, "MCP config without template")
    if any(
        (
            missing_configs,
            unreferenced_configs,
            missing_configs_for_templates,
            stray_templates,
        )
    ):
        return 1
    logger.info("MCP parity OK: %d configs, %d templates", len(configs), len(templates))
    return 0


if __name__ == "__main__":
    sys.exit(main())
