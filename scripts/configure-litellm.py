#!/usr/bin/env python3
"""Generate the gated, loopback-only LiteLLM proxy configuration."""

import argparse
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger  # noqa: E402 (sys.path must be set up first)
from cli_helpers import add_common_args  # noqa: E402
from env import load_env  # noqa: E402
from litellm_config import compute_model_list, write_config  # noqa: E402

GATE_ENV = "DOTFILES_RUN_LITELLM_SETUP"


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
            master_key_set = bool(os.environ.get("LITELLM_MASTER_KEY", "").strip())
            logger.info(
                "LiteLLM dry-run: models=%d master_key_set=%s config=%s",
                len(entries),
                master_key_set,
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
