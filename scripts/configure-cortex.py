#!/usr/bin/env python3
"""Generate Snowflake Cortex Code CLI configuration."""

import argparse
import json
import os
import shutil
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))
import logger
from cli_helpers import add_common_args
from file_utils import backup_file, write_text_file


def main():
    parser = argparse.ArgumentParser(
        description="Configure Snowflake Cortex Code CLI", allow_abbrev=False
    )
    add_common_args(parser)
    args = parser.parse_args()
    snowflake_home = Path(os.environ.get("SNOWFLAKE_HOME", "~/.snowflake")).expanduser()
    if not (snowflake_home / "connections.toml").exists() or not shutil.which("snow"):
        logger.info(
            "Cortex requires %s/connections.toml and the snow CLI — skipping configuration",
            snowflake_home,
        )
        return
    cortex_dir = snowflake_home / "cortex"
    model = os.environ.get("CORTEX_AGENT_MODEL", "auto")
    files = {
        cortex_dir
        / "settings.json": {
            "compactMode": True,
            "autoUpdate": True,
            "theme": "dark",
            "model": model,
        },
        cortex_dir
        / "permissions.json": {"defaultMode": "ask", "dangerouslyAllowAll": False},
        cortex_dir / "mcp.json": {"mcpServers": {}},
    }
    for path, data in files.items():
        if args.dry_run:
            logger.info("Would write %s", path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not args.no_backup:
            backup_file(str(path), enabled=True)
        write_text_file(str(path), json.dumps(data, indent=2) + "\n", backup=False)
    logger.info(
        "Cortex configured: %s (MCP servers can be added with `cortex mcp add <name> <commandOrUrl>`)",
        cortex_dir,
    )


if __name__ == "__main__":
    main()
