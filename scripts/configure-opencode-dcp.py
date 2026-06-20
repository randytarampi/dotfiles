#!/usr/bin/env python3
"""
OpenCode DCP TUI Plugin Configurator.

Generates/merges the DCP entry into ~/.config/opencode/tui.json so the /dcp
TUI panel (DCP v3.1.13+) is loaded. Core compression still loads via
opencode.json's plugin array — this script only owns the DCP entry in tui.json.

tui.json is a SHARED file: each TUI plugin has its own configure-opencode-*.py
that defensively merges only its own entry. This script does NOT touch voice
or any other plugin's entry.

Usage:
  configure-opencode-dcp.py [--dry-run] [--no-backup]

Environment variables:
  OPENCODE_DIR  - Override config directory
"""

import sys
import json
import argparse
import os
from typing import Any

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
LIB_DIR = SCRIPT_DIR if SCRIPT_DIR.endswith("lib") else os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger
from file_utils import backup_file, write_text_file
from env import load_env

DCP_PLUGIN_KEY = "@tarquinen/opencode-dcp"
DCP_PLUGIN_ENTRY = "@tarquinen/opencode-dcp@latest"


def is_dcp_entry(entry) -> bool:
    if isinstance(entry, str):
        return entry == DCP_PLUGIN_KEY or entry.startswith(DCP_PLUGIN_KEY + "@")
    if isinstance(entry, (list, tuple)) and entry:
        first = entry[0]
        return isinstance(first, str) and (
            first == DCP_PLUGIN_KEY or first.startswith(DCP_PLUGIN_KEY + "@")
        )
    return False


def main():
    parser = argparse.ArgumentParser(
        description="Configure OpenCode DCP plugin (tui.json)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print config to stdout without writing files",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip backup of existing tui.json",
    )
    args = parser.parse_args()

    opencode_dir = os.environ.get("OPENCODE_DIR")
    if opencode_dir:
        config_dir_path = os.path.abspath(os.path.expanduser(opencode_dir))
    else:
        config_dir_path = os.path.join(os.path.expanduser("~"), ".config", "opencode")

    env_path = os.path.join(config_dir_path, ".env")
    if not load_env(env_path):
        home_env = os.path.expanduser("~/.env")
        load_env(home_env)

    output_path = os.path.join(config_dir_path, "tui.json")

    existing_plugins: list[Any] = []
    existing_config: dict[str, Any] = {}
    if os.path.exists(output_path):
        try:
            with open(output_path, "r", encoding="utf-8") as f:
                loaded_config = json.load(f)
            if isinstance(loaded_config, dict):
                existing_config = loaded_config
                raw_plugins = existing_config.get("plugin", [])
                existing_plugins = (
                    list(raw_plugins) if isinstance(raw_plugins, (list, tuple)) else []
                )
        except Exception:
            existing_plugins = []
            existing_config = {}

    filtered_plugins = []
    for entry in existing_plugins:
        if is_dcp_entry(entry):
            continue
        filtered_plugins.append(entry)
    filtered_plugins.append(DCP_PLUGIN_ENTRY)

    tui_config: dict[str, Any] = (
        existing_config
        if existing_config
        else {
            "$schema": "https://opencode.ai/tui.json",
        }
    )
    tui_config["$schema"] = "https://opencode.ai/tui.json"
    tui_config["plugin"] = filtered_plugins

    if args.dry_run:
        print("# Dry-run: tui.json DCP entry")
        print(f"# Config dir: {config_dir_path}")
        print(json.dumps(tui_config.get("plugin", []), indent=2))
        return

    os.makedirs(config_dir_path, exist_ok=True)

    if os.path.exists(output_path) and not args.no_backup:
        backup_path = backup_file(output_path, enabled=True)
        if backup_path:
            logger.info(f"Backed up existing tui.json to {backup_path}")

    try:
        write_text_file(
            output_path,
            json.dumps(tui_config, indent=2) + "\n",
            backup=False,
        )
        logger.info(f"tui.json written to {output_path}")
    except Exception as e:
        logger.critical(f"Failed to write tui.json: {e}")
        sys.exit(1)

    summary_lines = [
        "DCP TUI plugin configured!",
        "",
        f"tui.json written to: {output_path}",
        "  • @tarquinen/opencode-dcp@latest (string form, /dcp panel)",
        "",
        "tui.json is a shared file — other TUI plugins (e.g. voice) are preserved.",
        "Core compression still loads via opencode.json plugin array (unchanged).",
    ]
    logger.info("\n".join(summary_lines))


if __name__ == "__main__":
    main()
