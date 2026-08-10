#!/usr/bin/env python3
"""
configure-secrets.py — Resolves machine-specific paths and secrets for AI tool configurations.
Writes sourceable .env files to target directories.
"""

import sys
import os
import argparse
import subprocess
import shutil

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger
from env import load_env
from idea import resolve_idea_mcp_server


def main():
    parser = argparse.ArgumentParser(
        description="Resolves machine-specific paths and secrets for AI tool configurations."
    )
    parser.add_argument("--target", help="Writes to a specific directory")
    parser.add_argument(
        "--all", action="store_true", help="Writes to all known AI tool dirs"
    )
    parser.add_argument(
        "--configure-jetbrains-ai",
        action="store_true",
        help="Also run configure-jetbrains-ai.py",
    )
    parser.add_argument(
        "--mode",
        choices=["global", "project"],
        default="global",
        help="Configuration mode (default: global)",
    )
    parser.add_argument(
        "--output",
        help="Output file for project mode (default: .env.local)",
    )

    args = parser.parse_args()

    if args.mode == "project":
        target_dirs = [os.path.dirname(os.path.abspath(args.output or ".env.local"))]
    else:
        target_dirs = None

    default_target = "~/.config/opencode"
    all_targets = [
        "~/.ai",
        "~/.air",
        "~/.codex",
        "~/.cursor",
        "~/.config/opencode",
        "~/.gemini",
    ]

    # Check env file location. DOTFILES_ENV_FILE is canonical; CREDENTIALS_FILE
    # remains as a backward-compatible alias for pre-chezmoi migration shells.
    credentials_file = os.environ.get("DOTFILES_ENV_FILE") or os.environ.get(
        "CREDENTIALS_FILE"
    )
    if not credentials_file:
        credentials_file = os.path.expanduser("~/.env")
    else:
        credentials_file = os.path.expanduser(credentials_file)

    # Determine target directories
    if target_dirs is not None:
        pass
    elif args.all:
        target_dirs = [os.path.expanduser(t) for t in all_targets]
    elif args.target:
        target_dirs = [os.path.expanduser(args.target)]
    elif os.environ.get("OPENCODE_DIR"):
        target_dirs = [os.path.expanduser(os.environ["OPENCODE_DIR"])]
    else:
        target_dirs = [os.path.expanduser(default_target)]

    # Source existing credentials if available
    if os.path.isfile(credentials_file):
        if load_env(credentials_file):
            logger.info(f"Sourced {credentials_file}")

    # 1. GitHub PAT
    logger.info("Resolving GitHub PAT...")
    gh_token = os.environ.get("GH_TOKEN", os.environ.get("GITHUB_TOKEN", ""))
    if gh_token:
        logger.info("GitHub PAT found in environment")
    elif shutil.which("gh"):
        try:
            res = subprocess.run(
                ["gh", "auth", "token"], capture_output=True, text=True, timeout=5
            )
            token = res.stdout.strip()
            if token:
                gh_token = token
                logger.info("GitHub PAT resolved from gh CLI")
            else:
                logger.warning("gh CLI authenticated but could not get token")
        except Exception:
            pass

    if not gh_token:
        logger.warning("No GitHub PAT found — set GH_TOKEN or run 'gh auth login'")

    # 2. Sentry token
    logger.info("Resolving Sentry token...")
    sentry_token = os.environ.get("SENTRY_AUTH_TOKEN", "")
    if sentry_token:
        logger.info("Sentry token found in environment")
    else:
        logger.warning(
            f"No Sentry token found — set SENTRY_AUTH_TOKEN in environment or {credentials_file}"
        )

    # 3. IntelliJ paths
    transport = os.environ.get("IJ_MCP_TRANSPORT", "sse")
    logger.info(f"Resolving IntelliJ MCP server paths (transport: {transport})...")

    java_bin, classpath = resolve_idea_mcp_server(transport)
    if java_bin and classpath:
        logger.info("IntelliJ MCP paths resolved (stdio mode)")
    else:
        if transport == "sse":
            logger.info("IntelliJ MCP using SSE transport (no java/classpath needed)")
        else:
            logger.warning(
                "IntelliJ IDEA not found or MCP server jars not detected — stdio mode won't work"
            )

    # 4. Meridian plugin path
    logger.info("Resolving Meridian plugin path...")
    meridian_plugin_path = ""
    npm_bin = shutil.which("npm")
    if npm_bin:
        try:
            res = subprocess.run(
                [npm_bin, "root", "-g"], capture_output=True, text=True, timeout=5
            )
            npm_root = res.stdout.strip()
            if npm_root:
                candidate = os.path.join(
                    npm_root, "@rynfar/meridian/plugin/meridian.ts"
                )
                if os.path.isfile(candidate):
                    meridian_plugin_path = candidate
                    logger.info(f"Meridian plugin found at {meridian_plugin_path}")
                else:
                    logger.warning(
                        "Meridian plugin not found at expected npm global path"
                    )
        except Exception:
            pass
    else:
        logger.warning("npm not found — cannot resolve Meridian plugin path")

    # 5. Additional MCP Template Vars
    logger.info("Resolving additional MCP environment variables...")
    shortcut_token = os.environ.get("SHORTCUT_API_TOKEN", "")
    mdb_client_id = os.environ.get("MDB_MCP_API_CLIENT_ID", "")
    mdb_client_secret = os.environ.get("MDB_MCP_API_CLIENT_SECRET", "")
    betterstack_token = os.environ.get("BETTERSTACK_API_TOKEN", "")

    # Write .env files
    for target_dir in target_dirs:
        logger.info(f"Processing target: {target_dir}")
        os.makedirs(target_dir, exist_ok=True)
        env_file = (
            os.path.abspath(args.output)
            if args.mode == "project" and args.output
            else os.path.join(target_dir, ".env")
        )

        env_content = f"""# Generated by configure-secrets.py
# Source this file before running configuration scripts.
#
# DO NOT COMMIT THIS FILE — it contains secrets and machine-specific paths.

export GH_TOKEN="{gh_token}"
export SENTRY_AUTH_TOKEN="{sentry_token}"
export IJ_MCP_SERVER_JAVA="{java_bin}"
export IJ_MCP_SERVER_CLASSPATH="{classpath}"
export MERIDIAN_PLUGIN_PATH="{meridian_plugin_path}"
export SHORTCUT_API_TOKEN="{shortcut_token}"
export MDB_MCP_API_CLIENT_ID="{mdb_client_id}"
export MDB_MCP_API_CLIENT_SECRET="{mdb_client_secret}"
export BETTERSTACK_API_TOKEN="{betterstack_token}"
"""

        try:
            from file_utils import write_text_file

            write_text_file(env_file, env_content, backup=False)
            logger.info(f"Environment file written to {env_file}")
        except Exception as e:
            logger.critical(f"Failed to write .env file {env_file}: {e}")

        # Gitignore handling
        if os.path.exists(os.path.join(target_dir, ".git")):
            gitignore_file = os.path.join(target_dir, ".gitignore")
            needs_append = True
            if os.path.isfile(gitignore_file):
                try:
                    with open(gitignore_file, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    if any(".env" in line for line in lines):
                        needs_append = False
                except Exception:
                    pass

            if needs_append:
                try:
                    with open(gitignore_file, "a", encoding="utf-8") as f:
                        f.write("\n.env\n")
                    logger.info(f"Added .env to {gitignore_file}")
                except Exception as e:
                    logger.warning(f"Failed to update {gitignore_file}: {e}")

    # Optionally configure JetBrains AI
    if args.configure_jetbrains_ai:
        logger.info("Configuring JetBrains AI...")
        configure_jb_py = os.path.join(SCRIPT_DIR, "configure-jetbrains-ai.py")
        try:
            subprocess.run([sys.executable, configure_jb_py, "--all"], check=True)
            logger.info("JetBrains AI configured")
        except Exception as e:
            logger.error(f"Failed to execute configure-jetbrains-ai.py: {e}")

    summary_lines = [
        "Environment resolved!",
        "",
        "Resolved Values:",
        f"  • GitHub PAT: {'set' if gh_token else 'missing'}",
        f"  • Sentry token: {'set' if sentry_token else 'missing'}",
    ]

    if transport == "stdio":
        summary_lines.append(f"  • IntelliJ JVM: {'set' if java_bin else 'missing'}")
        summary_lines.append(
            f"  • IntelliJ classpath: {'set' if classpath else 'missing'}"
        )
    else:
        summary_lines.append("  • IntelliJ MCP: SSE transport (no JVM needed)")

    summary_lines.append(
        f"  • Meridian plugin: {meridian_plugin_path if meridian_plugin_path else 'missing'}"
    )

    if shortcut_token:
        summary_lines.append("  • Shortcut token: set")
    if mdb_client_id:
        summary_lines.append("  • MongoDB Client ID: set")
    if mdb_client_secret:
        summary_lines.append("  • MongoDB Client Secret: set")
    if betterstack_token:
        summary_lines.append("  • BetterStack token: set")

    summary_lines.extend(["", "Environment resolution complete!"])

    logger.info("\n".join(summary_lines))


if __name__ == "__main__":
    main()
