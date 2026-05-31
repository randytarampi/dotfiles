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


def main():
    parser = argparse.ArgumentParser(
        description="Cleanly appends the Meridian plugin path to the OpenCode configuration."
    )
    args = parser.parse_args()

    opencode_dir = os.environ.get("OPENCODE_DIR")
    if opencode_dir:
        config_dir = os.path.abspath(os.path.expanduser(opencode_dir))
    else:
        config_dir = os.path.join(os.path.expanduser("~"), ".config", "opencode")

    config_path = os.path.join(config_dir, "opencode.json")
    meridian_host = os.environ.get("MERIDIAN_HOST", "127.0.0.1")
    meridian_port = os.environ.get("MERIDIAN_PORT", "3456")

    logger.info("Checking prerequisites...")

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
    meridian_plugin_path = ""
    npm_bin = shutil.which("npm")
    if npm_bin:
        try:
            result = subprocess.run(
                [npm_bin, "root", "-g"], capture_output=True, text=True, timeout=5
            )
            npm_root = result.stdout.strip()
            if npm_root:
                candidate = os.path.join(
                    npm_root, "@rynfar", "meridian", "plugin", "meridian.ts"
                )
                if os.path.isfile(candidate):
                    meridian_plugin_path = candidate
        except Exception:
            pass

    if not meridian_plugin_path:
        logger.critical(
            "Could not resolve meridian plugin path. Is @rynfar/meridian installed globally?"
        )
        sys.exit(1)

    logger.info(f"Adding meridian plugin to {config_path}...")

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        plugins = config.get("plugin", [])
        if meridian_plugin_path not in plugins:
            plugins.append(meridian_plugin_path)
            config["plugin"] = plugins
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)
                f.write("\n")
            logger.info("Meridian plugin path appended successfully")
        else:
            logger.info("Meridian plugin path already present in configuration")

    except Exception as e:
        logger.critical(f"Failed to update config: {e}")
        sys.exit(1)

    summary_lines = [
        "Meridian plugin configured!",
        "",
        f"Plugin path: {meridian_plugin_path}",
        f"Meridian proxy: http://{meridian_host}:{meridian_port}",
        "",
        "Verify meridian is running:",
        f"     curl http://{meridian_host}:{meridian_port}/health",
        "",
        "Configure script complete!",
    ]
    logger.info("\n".join(summary_lines))


if __name__ == "__main__":
    main()
