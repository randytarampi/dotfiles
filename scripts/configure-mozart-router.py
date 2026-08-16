#!/usr/bin/env python3
"""
configure-mozart-router.py — Configure Mozart AI router.
"""

import sys
import os
import json
import shutil
import subprocess
import argparse

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger
from env import load_env
from constants import check_ollama_daemon, get_ollama_local_base_url
from cli_helpers import add_common_args


def main():
    parser = argparse.ArgumentParser(description="Configure Mozart AI router.")
    add_common_args(parser)
    args = parser.parse_args()
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

    if not args.dry_run:
        os.makedirs(mozart_dir, exist_ok=True)

    # Check for mozart-router command
    mozart_bin = shutil.which("mozart-router")
    if not mozart_bin:
        npm_bin = shutil.which("npm")
        if npm_bin:
            logger.info("Installing mozart-router globally via npm...")
            try:
                if args.dry_run:
                    logger.info("Would install mozart-router globally via npm")
                else:
                    subprocess.run(
                        [npm_bin, "install", "-g", "mozart-router"], check=True
                    )
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
        with open(config_src, "r", encoding="utf-8") as f:
            config = json.load(f)

        # Resolve baseUrlEnv overrides and strip them from output
        gateways = config.get("gateways", {})
        for gw_name, gw_def in gateways.items():
            baseUrlEnv = gw_def.pop("baseUrlEnv", "")
            if baseUrlEnv:
                override = os.environ.get(baseUrlEnv, "").strip()
                if override:
                    gw_def["baseUrl"] = override.rstrip("/")

        # Resolve cloud proxy override: when local daemon is cloud-capable,
        # route ollama-cloud through the local daemon instead of direct cloud
        for gw_name, gw_def in gateways.items():
            cloud_proxy_env = gw_def.pop("cloudProxyEnv", "")
            if cloud_proxy_env:
                # Check if cloud proxy is enabled via env var (default: true)
                proxy_enabled = os.environ.get(cloud_proxy_env, "").strip().lower()
                if proxy_enabled not in ("0", "false"):
                    is_running, can_proxy_cloud = check_ollama_daemon()
                    if is_running and can_proxy_cloud:
                        logger.info(
                            f"Ollama daemon is cloud-capable; routing '{gw_name}' "
                            f"through local daemon at {get_ollama_local_base_url()}"
                        )
                        gw_def["baseUrl"] = get_ollama_local_base_url()
                        # Remove apiKeyEnv — local daemon handles auth transparently
                        gw_def.pop("apiKeyEnv", None)
                    else:
                        logger.info(
                            f"Ollama daemon not cloud-capable; '{gw_name}' "
                            f"uses direct cloud URL"
                        )

        if args.dry_run:
            logger.info(f"Would write Mozart router config to {config_dst}")
        else:
            with open(config_dst, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)
                f.write("\n")
            os.chmod(config_dst, 0o644)
        logger.info(f"Configured mozart-router at {config_dst}")
    except Exception as e:
        logger.critical(f"Failed to write config: {e}")
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
