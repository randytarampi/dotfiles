#!/usr/bin/env python3
"""configure-codegraph.py — Batch-initialize per-project CodeGraph indexes.

Scans for Git repos and opencode-configured projects under a root directory,
then runs `codegraph init` in each to create per-project .codegraph/ indexes.

Usage:
    configure-codegraph.py [--root DIR] [--dry-run] [--force]
"""

import argparse
import os
import shutil
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger
from env import load_env


def discover_projects(root):
    """Return sorted Git and OpenCode projects under root."""
    projects = set()
    for current_root, dirs, files in os.walk(root):
        current_path = os.path.abspath(current_root)
        is_git_repo = ".git" in dirs
        is_opencode_project = ".opencode" in dirs or "opencode.json" in files

        if is_git_repo or is_opencode_project:
            projects.add(current_path)

        if is_git_repo:
            dirs[:] = []

    return sorted(projects)


def main():
    parser = argparse.ArgumentParser(description="Batch-initialize CodeGraph indexes.")
    parser.add_argument(
        "--root",
        default="~/Development",
        help="Root directory to scan (default: ~/Development)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List discovered projects without indexing",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-index projects that already have a .codegraph directory",
    )
    args = parser.parse_args()

    if load_env():
        logger.info("Sourced environment from ~/.env")

    gate = os.environ.get("DOTFILES_RUN_CODEGRAPH_INDEX_SETUP", "0")
    if gate != "1":
        logger.info(
            f"DOTFILES_RUN_CODEGRAPH_INDEX_SETUP='{gate}' — skipping CodeGraph indexing"
        )
        return 0

    codegraph_cmd = shutil.which("codegraph")
    if not codegraph_cmd:
        logger.warning(
            "codegraph not found — install with: npm i -g @colbymchenry/codegraph"
        )
        return 0

    root = os.path.abspath(os.path.expanduser(args.root))
    if not os.path.isdir(root):
        logger.warning(f"Scan root does not exist or is not a directory: {root}")
        return 0

    projects = discover_projects(root)
    indexed = 0
    skipped = 0
    failed = 0

    for project_root in projects:
        codegraph_dir = os.path.join(project_root, ".codegraph")

        if args.dry_run:
            logger.info(f"Would index: {project_root}")
            continue

        if os.path.isdir(codegraph_dir) and not args.force:
            logger.info(f"Skipping existing CodeGraph index: {project_root}")
            skipped += 1
            continue

        if os.path.isdir(codegraph_dir):
            command = [codegraph_cmd, "index", "--force", project_root]
        else:
            command = [codegraph_cmd, "init", project_root]

        logger.info(f"Running {' '.join(command)}")
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode == 0:
            indexed += 1
        else:
            failed += 1
            details = result.stderr.strip() or result.stdout.strip()
            logger.warning(
                f"CodeGraph indexing failed for {project_root}: {details or 'unknown error'}"
            )

    mode = "Dry run" if args.dry_run else "CodeGraph indexing"
    summary_lines = [
        f"{mode} complete!",
        "",
        f"  Root:             {root}",
        f"  Projects found:   {len(projects)}",
        f"  Indexed:          {indexed}",
        f"  Skipped:          {skipped}",
        f"  Failed:           {failed}",
    ]
    logger.info("\n".join(summary_lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
