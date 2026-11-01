#!/usr/bin/env python3
"""
OpenCode DCP configuration reconciler.

Removes obsolete DCP entries from ~/.config/opencode/tui.json. Registration
and TUI loading are owned by the opencode.json plugin manifest.

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


def is_dcp_entry(entry) -> bool:
    if isinstance(entry, str):
        value = entry.removeprefix("-")
        return value == DCP_PLUGIN_KEY or value.startswith(DCP_PLUGIN_KEY + "@")
    if isinstance(entry, dict):
        return is_dcp_entry(entry.get("package", ""))
    if isinstance(entry, (list, tuple)) and entry:
        first = entry[0]
        return is_dcp_entry(first)
    return False


def main():
    parser = argparse.ArgumentParser(
        description="Remove obsolete DCP registration from tui.json."
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

    # Only remove the tui.json entry when the v2 replacement is actually in
    # place: opencode.json's plugins array must register DCP. A standalone
    # run against a config that lacks the manifest registration must not
    # strip DCP's only registration.
    replacement_registered = False
    opencode_json = os.path.join(config_dir_path, "opencode.json")
    try:
        with open(opencode_json, encoding="utf-8") as handle:
            oc_config = json.load(handle)
        oc_plugins = oc_config.get("plugins", []) if isinstance(oc_config, dict) else []
        if isinstance(oc_plugins, list):
            replacement_registered = any(is_dcp_entry(entry) for entry in oc_plugins)
        if not replacement_registered:
            oc_plugins = (
                oc_config.get("plugin", []) if isinstance(oc_config, dict) else []
            )
            if isinstance(oc_plugins, list):
                replacement_registered = any(
                    is_dcp_entry(entry) for entry in oc_plugins
                )
    except (OSError, json.JSONDecodeError) as error:
        logger.warning(
            "Could not confirm DCP registration in opencode.json (%s); "
            "keeping the tui.json DCP entry in place",
            error,
        )
    if not replacement_registered:
        logger.warning(
            "DCP is not registered in opencode.json's plugins array; "
            "not removing the tui.json DCP entry (replacement not working)"
        )
        return

    filtered_plugins = [entry for entry in existing_plugins if not is_dcp_entry(entry)]
    tui_config = existing_config or {}
    changed = filtered_plugins != existing_plugins
    if changed:
        tui_config["plugin"] = filtered_plugins

    if not changed:
        logger.info("No obsolete DCP tui.json registration to remove")
        return

    if args.dry_run:
        print("# Dry-run: remove obsolete tui.json DCP entry")
        print(f"# Config dir: {config_dir_path}")
        print(json.dumps(filtered_plugins, indent=2))
        return

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

    logger.info(
        "Removed obsolete DCP registration from tui.json; plugin loading is owned by opencode.json"
    )


if __name__ == "__main__":
    main()
