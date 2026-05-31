#!/usr/bin/env python3
"""
configure-mozart-router.py — Configure Mozart AI router.
"""

import sys
import os
import shutil
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger
from env import load_env


def main():
    if not load_env():
        logger.warning("~/.env not found")

    mozart_dir = os.environ.get("MOZART_DIR")
    if not mozart_dir:
        mozart_dir = os.path.expanduser("~/.mozart")
    else:
        mozart_dir = os.path.expanduser(mozart_dir)

    dotfiles_root = os.path.dirname(SCRIPT_DIR)
    config_src = os.path.join(dotfiles_root, "configs", "mozart-router", "mozart.json")
    config_dst = os.path.join(mozart_dir, "mozart.json")

    os.makedirs(mozart_dir, exist_ok=True)

    # Check for mozart-router command
    mozart_bin = shutil.which("mozart-router")
    if not mozart_bin:
        npm_bin = shutil.which("npm")
        if npm_bin:
            logger.info("Installing mozart-router globally via npm...")
            try:
                subprocess.run([npm_bin, "install", "-g", "mozart-router"], check=True)
            except Exception as e:
                logger.critical(f"Failed to install mozart-router: {e}")
                sys.exit(1)
        else:
            logger.critical("mozart-router not found and npm is unavailable")
            sys.exit(1)

    if not os.path.isfile(config_src):
        logger.critical(f"Missing config template: {config_src}")
        sys.exit(1)

    try:
        shutil.copy(config_src, config_dst)
        os.chmod(config_dst, 0o644)
        logger.info(f"Configured mozart-router at {config_dst}")
    except Exception as e:
        logger.critical(f"Failed to copy config: {e}")
        sys.exit(1)

    # Run doctor check
    try:
        res = subprocess.run(["mozart-router", "doctor"])
        if res.returncode == 0:
            logger.info("mozart-router doctor passed")
        else:
            logger.warning("mozart-router doctor reported issues")
    except Exception as e:
        logger.warning(f"Failed to execute mozart-router doctor: {e}")


if __name__ == "__main__":
    main()
