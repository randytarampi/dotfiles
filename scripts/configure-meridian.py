#!/usr/bin/env python3
"""
Configure Meridian Helper.
Adds the Meridian proxy plugin to an existing OpenCode configuration.
"""

import sys
import json
import argparse
import os
import shutil
import subprocess
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = SCRIPT_DIR if SCRIPT_DIR.endswith("lib") else os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger
from constants import MERIDIAN_DEFAULT_HOST, MERIDIAN_DEFAULT_PORT
from cli_helpers import add_common_args
from file_utils import backup_file, write_text_file
from meridian_path import (  # noqa: E402
    meridian_v2_qualification,
    resolve_meridian_binary,
    resolve_meridian_plugin_path,
)  # noqa: E402  # loader bootstraps scripts/lib.
import opencode_host  # noqa: E402


def reconcile_sdk_features(path, *, dry_run=False, no_backup=False):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            sdk_features = json.load(f)
        if not isinstance(sdk_features, dict):
            raise ValueError("sdk-features.json must contain a JSON object")
    else:
        sdk_features = {}
    opencode_features = sdk_features.get("opencode", {})
    if not isinstance(opencode_features, dict):
        raise ValueError("sdk-features.json opencode entry must be an object")
    desired = dict(sdk_features)
    desired["opencode"] = dict(opencode_features)
    desired["opencode"].update({"codeSystemPrompt": False, "clientSystemPrompt": True})
    content = json.dumps(desired, indent=2) + "\n"
    existing_content = None
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            existing_content = f.read()
    if not dry_run and content != existing_content:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path) and not no_backup:
            backup_file(path, enabled=True)
        write_text_file(path, content, backup=False)
    return content


def main():
    parser = argparse.ArgumentParser(
        description="Cleanly appends the Meridian plugin path to the OpenCode configuration."
    )
    add_common_args(parser, no_backup=True)
    args = parser.parse_args()

    opencode_dir = os.environ.get("OPENCODE_DIR")
    if opencode_dir:
        config_dir = os.path.abspath(os.path.expanduser(opencode_dir))
    else:
        config_dir = os.path.join(os.path.expanduser("~"), ".config", "opencode")

    config_path = os.path.join(config_dir, "opencode.json")
    meridian_host = os.environ.get("MERIDIAN_HOST", MERIDIAN_DEFAULT_HOST)
    meridian_port = os.environ.get("MERIDIAN_PORT", MERIDIAN_DEFAULT_PORT)

    logger.info("Checking prerequisites...")

    host_state, host_major = opencode_host.resolve_opencode_host()
    if host_state != "known" or host_major not in (1, 2):
        if os.environ.get("DOTFILES_RUN_MERIDIAN_SETUP", "0") == "1":
            logger.critical(
                f"Cannot configure Meridian for unverified OpenCode host ({host_state}, major {host_major})"
            )
            sys.exit(1)
        logger.info("Skipping Meridian setup: OpenCode host is unverified")
        return
    if host_state == "known" and host_major == 2:
        if os.environ.get("DOTFILES_RUN_MERIDIAN_SETUP", "0") != "1":
            logger.info("DOTFILES_RUN_MERIDIAN_SETUP='0' — skipping Meridian v2 setup")
            return
        opencode_bin = shutil.which("opencode")
        if not opencode_bin:
            logger.critical(
                "Meridian v2 setup failed: opencode binary is not resolvable"
            )
            sys.exit(1)
        # CLI binary qualification only: the desktop 2.2.0 rule applies to the
        # desktop-bundled binary (passed via --opencode-bin), never to a CLI
        # on any host platform.
        host_version = opencode_host.host_version_string()
        if not host_version or not meridian_v2_qualification(
            host_version, binary_form="cli"
        ):
            reported = host_version or "unknown"
            logger.critical(
                f"Meridian v2 setup failed hard: OpenCode host {reported} is not in the upstream qualified CLI set (2.0.16, 2.0.21, 2.0.31); desktop 2.2.0 darwin-arm64 applies only to the desktop-bundled binary. Deployment stops here until Meridian qualifies upstream or the owner decides otherwise."
            )
            sys.exit(1)
        meridian_bin = resolve_meridian_binary()
        if not meridian_bin:
            logger.critical(
                "Meridian v2 setup failed hard: meridian binary is not resolvable"
            )
            sys.exit(1)
        if args.dry_run:
            logger.info(
                "Would run Meridian v2 setup for OpenCode %s using %s",
                host_version,
                opencode_bin,
            )
            return
        try:
            result = subprocess.run(  # nosec B603 - meridian binary from resolve_meridian_binary(); fixed literal args.
                [meridian_bin, "setup", "--v2", "--opencode-bin", opencode_bin],
                check=False,
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            logger.critical("Meridian v2 setup timed out after 300 seconds")
            sys.exit(1)
        if result.returncode:
            logger.critical(
                "Meridian v2 setup failed with exit code %s", result.returncode
            )
            sys.exit(result.returncode)
        logger.info("Meridian v2 setup completed")
        sdk_features_path = os.path.join(
            os.path.expanduser("~"), ".config", "meridian", "sdk-features.json"
        )
        try:
            reconcile_sdk_features(
                sdk_features_path, dry_run=args.dry_run, no_backup=args.no_backup
            )
        except Exception as e:
            logger.critical("Failed to update sdk-features.json: %s", e)
            sys.exit(1)
        return

    if os.environ.get("DOTFILES_RUN_MERIDIAN_SETUP") == "1":
        logger.critical(
            "Meridian setup requires a probed OpenCode host; refusing legacy fallback."
        )
        sys.exit(1)

    if not os.path.exists(config_path):
        logger.critical(
            f"opencode.json not found at {config_path}\nRun configure-opencode.py first."
        )
        sys.exit(1)

    # Health check
    health_url = f"http://{meridian_host}:{meridian_port}/health"
    try:
        with urllib.request.urlopen(health_url, timeout=3) as response:
            if response.status != 200:
                raise Exception("Non-200 response")
    except Exception:
        logger.warning(f"Meridian not reachable at {health_url}")
        logger.warning("Run install-meridian.sh and start the service first.")
        logger.warning(
            "Plugin will still be added — Claude models won't work until meridian is running."
        )

    # Resolve plugin path
    meridian_plugin_path = resolve_meridian_plugin_path()

    if not meridian_plugin_path:
        logger.critical(
            "Could not resolve meridian plugin path. Is @rynfar/meridian installed globally?"
        )
        sys.exit(1)

    logger.info(f"Adding meridian plugin to {config_path}...")

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        plugin_key = "plugin" if host_major == 1 else "plugins"
        plugins = config.get(plugin_key, config.get("plugin", []))
        if not isinstance(plugins, list):
            raise ValueError("OpenCode plugins must be an array")
        installed = any(
            entry == meridian_plugin_path
            or (
                isinstance(entry, dict) and entry.get("package") == meridian_plugin_path
            )
            for entry in plugins
        )
        if not installed:
            plugins.append(meridian_plugin_path)
            if plugin_key != "plugin":
                config.pop("plugin", None)
            config[plugin_key] = plugins
            if args.dry_run:
                logger.info(f"Would append Meridian plugin path to {config_path}")
            else:
                if os.path.exists(config_path) and not args.no_backup:
                    backup_path = backup_file(config_path, enabled=True)
                    if backup_path:
                        logger.info(f"Backed up opencode.json to {backup_path}")
                with open(config_path, "w", encoding="utf-8") as f:
                    json.dump(config, f, indent=2)
                    f.write("\n")
                logger.info("Meridian plugin path appended successfully")
        else:
            logger.info("Meridian plugin path already present in configuration")

    except Exception as e:
        logger.critical(f"Failed to update config: {e}")
        sys.exit(1)

    meridian_config_dir = os.path.expanduser("~/.config/meridian")
    sdk_features_path = os.path.join(meridian_config_dir, "sdk-features.json")
    try:
        reconcile_sdk_features(
            sdk_features_path, dry_run=args.dry_run, no_backup=args.no_backup
        )
        if args.dry_run:
            logger.info(f"Would write sdk-features.json to {sdk_features_path}")
        else:
            logger.info(f"sdk-features.json written to {sdk_features_path}")
    except Exception as e:
        logger.critical(f"Failed to update sdk-features.json: {e}")
        sys.exit(1)

    summary_lines = [
        "Meridian plugin configured!",
        "",
        f"Plugin path: {meridian_plugin_path}",
        f"Meridian proxy: http://{meridian_host}:{meridian_port}",
        f"  • SDK features: {sdk_features_path} (opencode.codeSystemPrompt=false)",
        "",
        "Verify meridian is running:",
        f"     curl http://{meridian_host}:{meridian_port}/health",
        "",
        "Configure script complete!",
    ]
    logger.info("\n".join(summary_lines))


if __name__ == "__main__":
    main()
