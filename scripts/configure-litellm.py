#!/usr/bin/env python3
"""Generate the gated, loopback-only LiteLLM proxy configuration."""

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger
from cli_helpers import add_common_args
from env import load_env
from litellm_config import compute_model_list, write_config

GATE_ENV = "DOTFILES_RUN_LITELLM_SETUP"


def mask_secret(value):
    if not value:
        return "<unset>"
    return "<set>" if len(str(value)) < 8 else str(value)[:2] + "…" + str(value)[-4:]


def main():
    parser = argparse.ArgumentParser(
        description="Configure the loopback LiteLLM gateway."
    )
    add_common_args(parser)
    args = parser.parse_args()
    if not load_env():
        logger.warning("~/.env not found")
    if os.environ.get(GATE_ENV, "0") != "1":
        logger.info("%s is not enabled; skipping LiteLLM setup", GATE_ENV)
        return 0
    config_path = os.path.expanduser("~/.local/share/litellm/config.yaml")
    try:
        entries = compute_model_list()
        if args.dry_run:
            logger.info(
                "LiteLLM dry-run: models=%d master_key=%s config=%s",
                len(entries),
                mask_secret(os.environ.get("LITELLM_MASTER_KEY")),
                config_path,
            )
            return 0
        changed = write_config(config_path)
        logger.info(
            "LiteLLM config %s (%d model entries)",
            "updated" if changed else "unchanged",
            len(entries),
        )
        return 0
    except Exception as error:
        logger.error("LiteLLM configuration failed: %s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
