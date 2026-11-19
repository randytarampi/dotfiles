#!/usr/bin/env python3
"""
OpenCode DCP configuration reconciler.
Removes obsolete DCP entries from the shared TUI/client configs
(~/.config/opencode/tui.json and cli.json). Registration and loading are owned
by the opencode.json plugin manifest.
tui.json and cli.json are SHARED files: each TUI plugin has its own
configure-opencode-*.py that defensively merges only its own entry. This script
does NOT touch voice or any other plugin's entry.
Usage:
  configure-opencode-dcp.py [--dry-run] [--no-backup]
Environment variables:
  OPENCODE_DIR  - Override config directory
"""

import sys
import json
import argparse
import os
import tempfile
from typing import Any

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
LIB_DIR = SCRIPT_DIR if SCRIPT_DIR.endswith("lib") else os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)
import logger
from file_utils import backup_file
from env import load_env

DCP_PLUGIN_KEY = "@tarquinen/opencode-dcp"

# TUI/client config files that may carry a stale DCP entry. tui.json uses the
# singular "plugin" key; cli.json uses "plugins" and may have an array root.
# Both spellings are checked so a v1-era cli.json entry cannot survive.
DCP_CONFIG_TARGETS = (
    ("tui.json", ("plugin",)),
    ("cli.json", ("plugins", "plugin")),
)


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


def clean_entry_list(payload: Any, keys: tuple[str, ...]) -> tuple[Any, bool]:
    """Remove DCP entries from a config payload without touching other entries.

    Accepts either an array root (cli.json) or an object root whose plugin list
    lives under one of ``keys`` (tui.json uses "plugin", cli.json uses
    "plugins" and historically "plugin"). Returns the new payload and whether
    anything changed.
    """
    if isinstance(payload, list):
        filtered = [entry for entry in payload if not is_dcp_entry(entry)]
        return filtered, filtered != payload
    if isinstance(payload, dict):
        updated = dict(payload)
        changed = False
        for key in keys:
            raw_plugins = updated.get(key, [])
            existing = (
                list(raw_plugins) if isinstance(raw_plugins, (list, tuple)) else []
            )
            filtered = [entry for entry in existing if not is_dcp_entry(entry)]
            if filtered != existing:
                updated[key] = filtered
                changed = True
        return (updated, True) if changed else (payload, False)
    return payload, False


def write_config_atomic(path: str, payload: Any) -> None:
    """Replace one private shared config atomically; leave it intact on failure."""
    directory = os.path.dirname(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix=f".{os.path.basename(path)}.",
            dir=directory, delete=False,
        ) as handle:
            temporary = handle.name
            os.fchmod(handle.fileno(), 0o600)
            handle.write(json.dumps(payload, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(
        description="Remove obsolete DCP registration from tui.json and cli.json."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print config to stdout without writing files",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip backups of existing tui.json/cli.json",
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

    # Only remove the DCP entries when the v2 replacement is actually in place:
    # opencode.json's plugins array must register DCP. A standalone run against
    # a config that lacks the manifest registration must not strip DCP's only
    # registration.
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
            "keeping the shared config DCP entries in place",
            error,
        )

    if not replacement_registered:
        logger.warning(
            "DCP is not registered in opencode.json's plugins array; "
            "not removing the shared config DCP entries (replacement not working)"
        )
        return

    changed_files: list[str] = []
    for filename, keys in DCP_CONFIG_TARGETS:
        output_path = os.path.join(config_dir_path, filename)
        if not os.path.exists(output_path):
            continue
        try:
            with open(output_path, "r", encoding="utf-8") as handle:
                existing_payload = json.load(handle)
        except (OSError, json.JSONDecodeError) as error:
            logger.warning(
                "Could not read %s (%s); leaving it untouched", filename, error
            )
            continue

        updated_payload, changed = clean_entry_list(existing_payload, keys)
        if not changed:
            logger.info("No obsolete DCP %s registration to remove", filename)
            continue

        if args.dry_run:
            print(f"# Dry-run: remove obsolete DCP entry from {filename}")
            print(f"# Config dir: {config_dir_path}")
            print(json.dumps(updated_payload, indent=2))
            changed_files.append(filename)
            continue

        # A shared client config may be a user-managed symlink. Never replace
        # the link itself or follow a .bak symlink into an unrelated file.
        if os.path.islink(output_path) or (
            not args.no_backup and os.path.islink(output_path + ".bak")
        ):
            logger.critical(
                "Refusing to rewrite symlinked shared config or backup: %s", output_path
            )
            sys.exit(1)
        if not args.no_backup:
            backup_path = backup_file(output_path, enabled=True)
            if not backup_path:
                logger.critical(
                    "Could not back up %s; refusing to rewrite shared config", filename
                )
                sys.exit(1)
            logger.info(f"Backed up existing {filename} to {backup_path}")
        try:
            write_config_atomic(output_path, updated_payload)
            logger.info(f"{filename} written to {output_path}")
            changed_files.append(filename)
        except Exception as e:
            logger.critical(f"Failed to write {filename}: {e}")
            sys.exit(1)

    if changed_files:
        verb = "Would remove" if args.dry_run else "Removed"
        logger.info(
            "%s obsolete DCP registration from %s; plugin loading is owned by opencode.json",
            verb,
            ", ".join(changed_files),
        )


if __name__ == "__main__":
    main()
