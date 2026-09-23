#!/usr/bin/env python3
"""Seed a non-secret AWS CLI configuration baseline."""

import argparse
import configparser
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(os.path.dirname(os.path.realpath(__file__)))
LIB_DIR = SCRIPT_DIR / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import logger
from cli_helpers import add_common_args
from env import load_env


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed a non-secret AWS CLI configuration baseline",
        allow_abbrev=False,
    )
    add_common_args(parser)
    return parser.parse_args()


def validate_existing(path: Path) -> None:
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, OSError) as exc:
        logger.error("AWS config is malformed and was not changed: %s (%s)", path, exc)
        raise SystemExit(1) from exc
    logger.info("AWS config already exists and parses: %s", path)


def main() -> None:
    args = parse_args()
    load_env()
    gate = os.environ.get("DOTFILES_RUN_AWS_CONFIG_SETUP", "0")
    if gate != "1":
        logger.info(
            "DOTFILES_RUN_AWS_CONFIG_SETUP='%s' — skipping AWS CLI configuration", gate
        )
        return

    path = Path.home() / ".aws" / "config"
    if path.exists():
        validate_existing(path)
        return

    region = (
        os.environ.get("DOTFILES_AWS_REGION", "").strip()
        or os.environ.get("AWS_REGION", "").strip()
        or "us-east-1"
    )
    if (
        not os.environ.get("DOTFILES_AWS_REGION", "").strip()
        and not os.environ.get("AWS_REGION", "").strip()
    ):
        logger.warning("DOTFILES_AWS_REGION and AWS_REGION are unset; using us-east-1")
    content = f"[default]\nregion = {region}\noutput = json\n"
    if args.dry_run:
        logger.info("Would write AWS CLI config: %s", path)
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        logger.error("Failed to write AWS config %s: %s", path, exc)
        raise SystemExit(1) from exc
    logger.info("Wrote AWS CLI config: %s", path)


if __name__ == "__main__":
    main()
