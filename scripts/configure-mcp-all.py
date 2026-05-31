#!/usr/bin/env python3
"""
configure-mcp-all.py — Generate MCP configs for ALL AI tools.
Iterates over all tools defined in global-mcps.json and calls configure-mcp-tool.py
"""

import sys
import os
import json
import argparse
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger


def main():
    parser = argparse.ArgumentParser(
        description="Generate MCP configurations for all AI tools from centralized templates."
    )
    parser.add_argument(
        "--mode",
        choices=["global", "project"],
        default="global",
        help="Config mode (default: global)",
    )
    parser.add_argument(
        "--project-dir",
        default=os.getcwd(),
        help="Project directory for project-mode (default: cwd)",
    )
    parser.add_argument(
        "--project-mcps",
        default="",
        help="Comma-separated list of project MCP template names",
    )
    parser.add_argument(
        "--tools", default="", help="Only generate for these tools (comma-separated)"
    )
    parser.add_argument(
        "--include",
        default="",
        help="Only include MCP templates matching these comma-separated globs",
    )
    parser.add_argument(
        "--exclude",
        default="",
        help="Exclude MCP templates matching these comma-separated globs",
    )
    parser.add_argument(
        "--env-file",
        default="",
        help="Path to .env file with secrets (default: auto-detect)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview all configs without writing"
    )
    backup_group = parser.add_mutually_exclusive_group()
    backup_group.add_argument(
        "--backup",
        action="store_true",
        dest="backup",
        default=True,
        help="Create .bak of existing configs (default)",
    )
    backup_group.add_argument(
        "--no-backup", action="store_false", dest="backup", help="Skip backups"
    )

    args = parser.parse_args()

    dotfiles_dir = os.path.dirname(SCRIPT_DIR)
    registry_file = os.path.join(dotfiles_dir, "configs", "mcp", "global-mcps.json")

    if not os.path.isfile(registry_file):
        logger.critical(f"Error: registry not found at {registry_file}")
        sys.exit(1)

    try:
        with open(registry_file, "r", encoding="utf-8") as f:
            registry = json.load(f)
        all_tools = list(registry.get("tools", {}).keys())
    except Exception as e:
        logger.critical(f"Failed to read registry: {e}")
        sys.exit(1)

    if args.tools:
        tools = [t.strip() for t in args.tools.split(",") if t.strip()]
    else:
        tools = all_tools

    # Build common arguments
    common_args = ["--mode", args.mode]
    if args.project_dir:
        common_args.extend(["--project-dir", args.project_dir])
    if args.project_mcps:
        common_args.extend(["--project-mcps", args.project_mcps])
    if args.env_file:
        common_args.extend(["--env-file", args.env_file])
    if args.include:
        common_args.extend(["--include", args.include])
    if args.exclude:
        common_args.extend(["--exclude", args.exclude])
    if args.dry_run:
        common_args.append("--dry-run")
    if args.backup:
        common_args.append("--backup")
    else:
        common_args.append("--no-backup")

    success = 0
    fail = 0

    configure_tool_py = os.path.join(SCRIPT_DIR, "configure-mcp-tool.py")

    for tool in tools:
        if not tool:
            continue
        logger.info(f"── {tool} ──")
        cmd = [sys.executable, configure_tool_py] + common_args + [tool]
        try:
            res = subprocess.run(cmd)
            if res.returncode == 0:
                success += 1
            else:
                logger.error(f"  ✗ {tool} failed (exit {res.returncode})")
                fail += 1
        except Exception as e:
            logger.error(f"  ✗ {tool} failed to run: {e}")
            fail += 1

    logger.info("")
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info(f"{success} tools configured")
    if fail > 0:
        logger.error(f"{fail} tools failed")
    logger.info(f"  Mode: {args.mode}")
    if args.dry_run:
        logger.info("  (dry-run: no files written)")
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    if fail > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
