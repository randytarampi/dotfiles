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
from cli_helpers import (
    add_common_args,
    add_skip_arg,
    add_local_fallback_args,
    add_min_reasoning_embedding_arg,
    forward_min_reasoning_embedding_arg,
    forward_local_fallback_args,
    parse_skip,
)


def main():
    parser = argparse.ArgumentParser(
        description="Configure JetBrains AI tools (Junie, AI Assistant)."
    )
    add_common_args(parser)

    add_skip_arg(parser, ["models", "dirs"])
    parser.add_argument(
        "--project-dir", default=os.getcwd(), help="Project directory (default: cwd)"
    )
    add_local_fallback_args(parser)
    add_min_reasoning_embedding_arg(parser)
    args = parser.parse_args()
    failures = 0

    skipped = parse_skip(args.skip, ["models", "dirs"])
    do_models = "models" not in skipped
    do_dirs = "dirs" not in skipped

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
        if args.dry_run:
            logger.info("Would create AI directories and symlinks")
        else:
            ensure_ai_dirs(project_root)

    if do_models:
        local_fallback_args = forward_local_fallback_args(args)
        if args.dry_run:
            logger.info(f"Would generate JetBrains model profiles in {target_dir}")
            if local_fallback_args:
                logger.info(
                    "Would forward local fallback args: "
                    + " ".join(local_fallback_args)
                )
            continue_models = True
        else:
            os.makedirs(target_dir, exist_ok=True)
            continue_models = True
        if continue_models:
            local_models = list_local_ollama_models()
            local_model_names = [
                m["name"] if isinstance(m, dict) else str(m) for m in local_models
            ]
            local_models_str = " ".join(local_model_names)

            generate_profiles_py = os.path.join(
                SCRIPT_DIR, "generate-jetbrains-profiles.py"
            )
            cmd = (
                [
                    sys.executable,
                    generate_profiles_py,
                    "--groups-json",
                    groups_json,
                    "--target-dir",
                    target_dir,
                    "--local-models",
                    local_models_str,
                    *(["--dry-run"] if args.dry_run else []),
                ]
                + local_fallback_args
                + forward_min_reasoning_embedding_arg(args)
            )
            try:
                res = subprocess.run(cmd)
                if res.returncode != 0:
                    logger.error(
                        f"Failed to generate JetBrains model profiles (exit {res.returncode})"
                    )
                    failures += 1
            except Exception as e:
                logger.error(f"Failed to run profiles generation helper: {e}")
                failures += 1

    summary_lines = [
        "JetBrains AI configured!",
        "",
        "  Model profiles:  ~/.junie/models/",
        f"  .junie symlink:   {project_root}/.junie → .ai",
        f"  .aiassistant:     {project_root}/.aiassistant/rules → .ai/rules",
        "",
        "JetBrains AI configure complete!",
    ]
    logger.info("\n".join(summary_lines))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
