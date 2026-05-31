#!/usr/bin/env python3
"""
Get Tools Helper.
Accepts registry file path, returns a comma-separated list of keys from the `tools` dictionary.
"""

import sys
import json
import argparse
import os

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = SCRIPT_DIR if SCRIPT_DIR.endswith("lib") else os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger


def main():
    parser = argparse.ArgumentParser(
        description="Extracts comma-separated tool keys from global-mcps registry."
    )
    parser.add_argument("registry_path", help="Path to the global-mcps.json file")
    args = parser.parse_args()

    try:
        with open(args.registry_path, "r", encoding="utf-8") as f:
            registry = json.load(f)

        tools = registry.get("tools", {})
        print(",".join(tools.keys()))
    except Exception as e:
        logger.critical(f"Failed to read registry: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
