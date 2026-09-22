#!/usr/bin/env python3
"""Seed a non-secret Docker CLI configuration baseline."""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(os.path.dirname(os.path.realpath(__file__)))
LIB_DIR = SCRIPT_DIR / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import logger
from cli_helpers import add_common_args
from env import load_env


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed a non-secret Docker CLI configuration baseline",
        allow_abbrev=False,
    )
    add_common_args(parser, no_backup=True)
    return parser.parse_args()


def validate_existing(path: Path) -> None:
    try:
        with path.open(encoding="utf-8") as handle:
            json.load(handle)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        logger.error(
            "Docker config is malformed and was not changed: %s (%s)", path, exc
        )
        raise SystemExit(1) from exc
    logger.info("Docker config already exists and parses: %s", path)


def main() -> None:
    args = parse_args()
    load_env()
    gate = os.environ.get("DOTFILES_RUN_DOCKER_CONFIG_SETUP", "0")
    if gate != "1":
        logger.info(
            "DOTFILES_RUN_DOCKER_CONFIG_SETUP='%s' — skipping Docker configuration",
            gate,
        )
        return

    path = Path.home() / ".docker" / "config.json"
    if path.exists():
        validate_existing(path)
        return

    if shutil.which("docker") is None:
        logger.warning(
            "Docker is not installed; writing daemon-independent config baseline"
        )

    content: dict[str, Any] = {"auths": {}}
    if shutil.which("docker-credential-desktop") is not None:
        content["credsStore"] = "desktop"
    else:
        logger.info(
            "docker-credential-desktop is unavailable; omitting credsStore from Docker config"
        )
    content["currentContext"] = "default"

    if args.dry_run:
        logger.info("Would write Docker config: %s", path)
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(content, handle, indent=2)
            handle.write("\n")
    except OSError as exc:
        logger.error("Failed to write Docker config %s: %s", path, exc)
        raise SystemExit(1) from exc
    logger.info("Wrote Docker config: %s", path)


if __name__ == "__main__":
    main()
