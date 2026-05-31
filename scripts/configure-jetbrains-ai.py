#!/usr/bin/env python3
"""
configure-jetbrains-ai.py — Configures JetBrains AI tools (Junie, AI Assistant).
"""

import sys
import os
import argparse
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger
from env import load_env
from ai_dirs import ensure_ai_dirs
from discover_models import list_local_ollama_models


def main():
    parser = argparse.ArgumentParser(
        description="Configure JetBrains AI tools (Junie, AI Assistant)."
    )

    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--models", action="store_true", help="Only generate model profiles"
    )
    group.add_argument(
        "--dirs", action="store_true", help="Only create AI dirs + symlinks"
    )
    group.add_argument("--mcp", action="store_true", help="Only write MCP config")
    group.add_argument(
        "--all",
        action="store_true",
        help="All: models, dirs, MCP (default: models + dirs if none specified)",
    )

    parser.add_argument(
        "--all-tools", action="store_true", help="All + configure-mcp-all.py"
    )
    parser.add_argument(
        "--tools", default="", help="Comma-separated tool list for --all-tools"
    )
    parser.add_argument(
        "--project-mcps", default="", help="Comma-separated project MCP template names"
    )
    parser.add_argument(
        "--project-dir", default=os.getcwd(), help="Project directory (default: cwd)"
    )

    args = parser.parse_args()

    # Determine what actions to take
    # If --all-tools is set, it implies --all.
    # Otherwise, if none of --models, --dirs, --mcp, --all is specified, default to models + dirs.
    do_models = False
    do_dirs = False
    do_mcp = False

    if args.all_tools or args.all:
        do_models = True
        do_dirs = True
        do_mcp = True
    elif args.models:
        do_models = True
    elif args.dirs:
        do_dirs = True
    elif args.mcp:
        do_mcp = True
    else:
        # Default behavior: models + dirs
        do_models = True
        do_dirs = True

    # Load environment variables
    if not load_env():
        logger.warning("~/.env not found, skipping env load")

    project_root = os.path.abspath(os.path.expanduser(args.project_dir))
    groups_json = os.path.join(
        SCRIPT_DIR, "..", "configs", "junie", "model-groups.json"
    )
    target_dir = os.path.expanduser("~/.junie/models")

    if not os.path.isfile(groups_json):
        logger.critical(f"model-groups.json not found at {groups_json}")
        sys.exit(1)

    if do_dirs:
        logger.info(f"Ensuring AI directories at {project_root}...")
        ensure_ai_dirs(project_root)

    if do_mcp:
        logger.info("Generating Junie MCP config...")
        mcp_args = ["--mode", "project", "--project-dir", project_root, "--no-backup"]
        if args.project_mcps:
            mcp_args.extend(["--project-mcps", args.project_mcps])

        configure_mcp_tool_py = os.path.join(SCRIPT_DIR, "configure-mcp-tool.py")
        cmd = [sys.executable, configure_mcp_tool_py] + mcp_args + ["junie"]
        try:
            res = subprocess.run(cmd)
            if res.returncode == 0:
                logger.info(".ai/mcp/mcp.json written")
            else:
                logger.error(
                    f"Failed to generate Junie MCP config (exit {res.returncode})"
                )
        except Exception as e:
            logger.error(f"Failed to run mcp configuration tool: {e}")

    if args.all_tools:
        logger.info("Running configure-mcp-all.py for other AI tools...")
        mcp_all_args = ["--mode", "project", "--project-dir", project_root]
        if args.tools:
            mcp_all_args.extend(["--tools", args.tools])
        if args.project_mcps:
            mcp_all_args.extend(["--project-mcps", args.project_mcps])

        configure_mcp_all_py = os.path.join(SCRIPT_DIR, "configure-mcp-all.py")
        cmd = [sys.executable, configure_mcp_all_py] + mcp_all_args
        try:
            res = subprocess.run(cmd)
            if res.returncode == 0:
                logger.info("Additional tools configured")
            else:
                logger.error(
                    f"Failed configure-mcp-all.py execution (exit {res.returncode})"
                )
        except Exception as e:
            logger.error(f"Failed to run configure-mcp-all.py: {e}")

    if do_models:
        os.makedirs(target_dir, exist_ok=True)
        local_models = list_local_ollama_models()
        local_models_str = " ".join(local_models)

        generate_profiles_py = os.path.join(
            SCRIPT_DIR, "generate-jetbrains-profiles.py"
        )
        cmd = [
            sys.executable,
            generate_profiles_py,
            "--groups-json",
            groups_json,
            "--target-dir",
            target_dir,
            "--local-models",
            local_models_str,
        ]
        try:
            res = subprocess.run(cmd)
            if res.returncode != 0:
                logger.error(
                    f"Failed to generate JetBrains model profiles (exit {res.returncode})"
                )
        except Exception as e:
            logger.error(f"Failed to run profiles generation helper: {e}")

    summary_lines = [
        "JetBrains AI configured!",
        "",
        "  Model profiles:  ~/.junie/models/",
        f"  MCP config:      {project_root}/.ai/mcp/mcp.json",
        f"  .junie symlink:   {project_root}/.junie → .ai",
        f"  .aiassistant:     {project_root}/.aiassistant/rules → .ai/rules",
        "",
        "JetBrains AI configure complete!",
    ]
    logger.info("\n".join(summary_lines))


if __name__ == "__main__":
    main()
