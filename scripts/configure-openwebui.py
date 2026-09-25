#!/usr/bin/env python3
"""Configure and reconcile the opt-in Open WebUI deployment."""

import argparse
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger
from cli_helpers import add_common_args
from env import load_env
from openwebui import (
    OpenWebUIClient,
    OpenWebUIError,
    compute_desired_state,
    emit_env,
    reconcile,
    reconcile_via_api,
)

GATE_ENV = "DOTFILES_RUN_OPENWEBUI_SETUP"


def _parser():
    parser = argparse.ArgumentParser(description="Configure Open WebUI connections.")
    add_common_args(parser)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--emit-env", action="store_true")
    mode.add_argument("--reconcile", action="store_true")
    mode.add_argument("--check", action="store_true")
    return parser


def main():
    args = _parser().parse_args()
    if not load_env():
        logger.warning("~/.env not found")
    if os.environ.get(GATE_ENV, "0") != "1":
        logger.info("%s is not enabled; skipping Open WebUI setup", GATE_ENV)
        return 0

    desired = compute_desired_state()
    if args.emit_env:
        logger.info("%s", emit_env(desired, masked=True))
        logger.info("WEBUI_SECRET_KEY is supplied by the LaunchAgent environment")
        return 0

    base_url = f"http://127.0.0.1:{os.environ.get('OPENWEBUI_PORT', '8080')}"
    if not (args.reconcile or args.check):
        args.reconcile = True
    if not os.environ.get("OPENWEBUI_API_KEY", "").strip():
        logger.warning("OPENWEBUI_API_KEY is absent; skipping Open WebUI API operation")
        return 0
    client = OpenWebUIClient(base_url, os.environ["OPENWEBUI_API_KEY"])
    if not client.health_check():
        logger.warning("Open WebUI is not healthy at %s; skipping", base_url)
        return 0
    try:
        if args.check or args.dry_run:
            result = reconcile(
                client.get_openai_config(), client.get_ollama_config(), desired
            )
            logger.info(result.summary())
            drift = result.status != "clean" or any(
                item["action"] != "keep" for item in result.plan.entries
            )
            if args.dry_run:
                return 1 if result.status == "collision" else 0
            return 1 if drift else 0
        result = reconcile_via_api(client, desired)
        logger.info(result.summary())
        return 0
    except OpenWebUIError as error:
        logger.error("Open WebUI operation failed: %s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
