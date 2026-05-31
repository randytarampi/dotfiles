#!/usr/bin/env python3
"""
configure-opencode-project.py — Writes project-specific OpenCode config overrides.
"""

import sys
import os
import argparse
import subprocess
import tempfile
import shutil

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger
from env import load_env


def main():
    parser = argparse.ArgumentParser(
        description="Writes project-specific OpenCode config overrides."
    )
    parser.add_argument(
        "--preset", default="pro-plus", help="Preset to use (default: pro-plus)"
    )
    parser.add_argument(
        "--all-tools", action="store_true", help="Configure other AI tools as well"
    )
    parser.add_argument(
        "--tools", default="", help="Comma-separated tool list for --all-tools"
    )
    parser.add_argument(
        "--project-mcps", default="", help="Comma-separated project MCP template names"
    )

    args = parser.parse_args()

    project_root = os.getcwd()

    # Source .env for global secrets if available
    global_env = os.path.expanduser("~/.config/opencode/.env")
    if load_env(global_env):
        logger.info(f"Sourced global env: {global_env}")

    # Source project .env for project-specific secrets if available
    dotopencode_dir = os.path.join(project_root, ".opencode")
    project_env = os.path.join(dotopencode_dir, ".env")
    if load_env(project_env):
        logger.info(f"Sourced project env: {project_env}")

    # Step 1: Generate project opencode.json in a temp dir, then move to project root
    temp_dir = tempfile.mkdtemp()
    try:
        configure_opencode_py = os.path.join(SCRIPT_DIR, "configure-opencode.py")
        cmd_args = [
            sys.executable,
            configure_opencode_py,
            "--mode",
            "project",
            "--preset",
            args.preset,
        ]

        # We set OPENCODE_DIR to temp_dir for the child process so it outputs opencode.json there
        env = os.environ.copy()
        env["OPENCODE_DIR"] = temp_dir

        res = subprocess.run(cmd_args, env=env, capture_output=True, text=True)
        # Re-route output, replacing temp_dir with project_root for clean display
        stdout = res.stdout.replace(temp_dir, project_root)
        stderr = res.stderr.replace(temp_dir, project_root)

        if stdout:
            print(stdout, end="")
        if stderr:
            print(stderr, end="", file=sys.stderr)

        temp_opencode = os.path.join(temp_dir, "opencode.json")
        if os.path.isfile(temp_opencode):
            shutil.copy(temp_opencode, os.path.join(project_root, "opencode.json"))
            logger.info(f"opencode.json written to {project_root}/opencode.json")
        else:
            logger.warning("opencode.json was not generated")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    # Step 2: Set project-tier preset in oh-my-opencode-slim.json
    os.makedirs(dotopencode_dir, exist_ok=True)
    opencode_tier_py = os.path.join(SCRIPT_DIR, "configure-opencode-tier.py")

    tier_args = [sys.executable, opencode_tier_py, args.preset]
    # Optionally append local fallbacks if DOTFILES_USE_LOCAL_OLLAMA is 1
    if os.environ.get("DOTFILES_USE_LOCAL_OLLAMA", "1") == "1":
        tier_args.insert(2, "--with-local-fallbacks")

    env = os.environ.copy()
    env["OPENCODE_DIR"] = dotopencode_dir

    logger.info(f"Running tier switcher on project dir with preset {args.preset}...")
    subprocess.run(tier_args, env=env)

    summary_lines = [
        "Project OpenCode config written!",
        "",
        "  Config locations:",
        f"    • opencode.json               → {project_root}/opencode.json",
        f"    • oh-my-opencode-slim.json     → {dotopencode_dir}/oh-my-opencode-slim.json",
        "",
        "  Note: Project config EXTENDS the global config.",
        "  Global config: ~/.config/opencode/",
        "",
        "Project configure complete!",
    ]
    logger.info("\n".join(summary_lines))

    # Step 3: Configure other AI tools if --all-tools was passed
    if args.all_tools:
        logger.info("Running configure-mcp-all.py for other AI tools...")
        mcp_args = ["--mode", "project"]
        if args.tools:
            mcp_args.extend(["--tools", args.tools])
        if args.project_mcps:
            mcp_args.extend(["--project-mcps", args.project_mcps])

        configure_mcp_all_py = os.path.join(SCRIPT_DIR, "configure-mcp-all.py")
        subprocess.run([sys.executable, configure_mcp_all_py] + mcp_args)
        logger.info("Additional tools configured")


if __name__ == "__main__":
    main()
