#!/usr/bin/env python3
"""
Configure Meridian Helper.
Configures Meridian proxy settings used by non-OpenCode tools.
"""

import sys
import json
import argparse
import os
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = SCRIPT_DIR if SCRIPT_DIR.endswith("lib") else os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger
from constants import MERIDIAN_DEFAULT_HOST, MERIDIAN_DEFAULT_PORT
from cli_helpers import add_common_args
from file_utils import backup_file, write_text_file


def main():
    parser = argparse.ArgumentParser(
        description="Configure Meridian proxy settings used by non-OpenCode tools."
    )
    add_common_args(parser, no_backup=True)
    args = parser.parse_args()

    meridian_host = os.environ.get("MERIDIAN_HOST", MERIDIAN_DEFAULT_HOST)
    meridian_port = os.environ.get("MERIDIAN_PORT", MERIDIAN_DEFAULT_PORT)

    logger.info("Checking prerequisites...")

    # Health check
    health_url = f"http://{meridian_host}:{meridian_port}/health"
    try:
        with urllib.request.urlopen(health_url, timeout=3) as response:
            if response.status != 200:
                raise Exception("Non-200 response")
    except Exception:
        logger.warning(f"Meridian not reachable at {health_url}")
        logger.warning("Run install-meridian.sh and start the service first.")
    meridian_config_dir = os.path.expanduser("~/.config/meridian")
    sdk_features_path = os.path.join(meridian_config_dir, "sdk-features.json")
    try:
        if os.path.exists(sdk_features_path):
            with open(sdk_features_path, "r", encoding="utf-8") as f:
                sdk_features = json.load(f)
        else:
            sdk_features = {}

        if not isinstance(sdk_features, dict):
            sdk_features = {}

        opencode_features = sdk_features.get("opencode", {})
        if not isinstance(opencode_features, dict):
            opencode_features = {}
        opencode_features.update(
            {
                "codeSystemPrompt": False,
                "clientSystemPrompt": True,
            }
        )
        sdk_features["opencode"] = opencode_features
        sdk_features_content = json.dumps(sdk_features, indent=2) + "\n"

        if args.dry_run:
            logger.info(f"Would write sdk-features.json to {sdk_features_path}")
        else:
            os.makedirs(meridian_config_dir, exist_ok=True)
            if os.path.exists(sdk_features_path) and not args.no_backup:
                backup_path = backup_file(sdk_features_path, enabled=True)
                if backup_path:
                    logger.info(
                        f"Backed up existing sdk-features.json to {backup_path}"
                    )
            write_text_file(sdk_features_path, sdk_features_content, backup=False)
            logger.info(f"sdk-features.json written to {sdk_features_path}")
    except Exception as e:
        logger.critical(f"Failed to update sdk-features.json: {e}")
        sys.exit(1)

    summary_lines = [
        "Meridian proxy settings configured!",
        "",
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
