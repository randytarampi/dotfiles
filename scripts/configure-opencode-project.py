#!/usr/bin/env python3
"""
configure-opencode-project.py — Writes project-specific OpenCode config overrides.

Steps:
  1. opencode    — Generate project opencode.json (always runs)
  2. tier        — Switch oh-my-opencode-slim preset for the project
  3. codegraph   — Run codegraph init -i for the project
  4. mcps        — Configure MCPs for other AI tools (via configure-mcp-all.py)

By default all steps run. Use --steps to select specific steps.
Use --all-tools as shorthand for --steps with mcps included.
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
from opencode_config import build_tier_args

ALL_STEPS = ["opencode", "tier", "codegraph", "mcps"]
DEFAULT_STEPS = ["opencode", "tier", "codegraph"]


def step_name(s):
    """Validate and normalize a step name."""
    s = s.strip().lower()
    if s not in ALL_STEPS:
        raise argparse.ArgumentTypeError(
            f"Unknown step '{s}'. Choose from: {', '.join(ALL_STEPS)}"
        )
    return s


def main():
    parser = argparse.ArgumentParser(
        description="Writes project-specific OpenCode config overrides."
    )
    parser.add_argument(
        "--preset", default="pro-plus", help="Preset to use (default: pro-plus)"
    )
    parser.add_argument(
        "--steps",
        default=None,
        help=(
            f"Comma-separated steps to run (default: {','.join(DEFAULT_STEPS)}). "
            f"Available: {', '.join(ALL_STEPS)}. 'opencode' is always included."
        ),
    )
    parser.add_argument(
        "--all-mcps",
        action="store_true",
        help="Shorthand to include the 'mcps' step (configure MCPs for other AI platforms)",
    )
    parser.add_argument(
        "--mcps",
        default="",
        help="Comma-separated AI platform list for the mcps step, e.g. cursor,ai (passed to configure-mcp-all.py --tools)",
    )
    parser.add_argument(
        "--project-mcps",
        default="",
        help="Comma-separated project MCP template names (passed to configure-mcp-all.py)",
    )
    parser.add_argument(
        "--local-fallback-preset",
        default=None,
        help="Which local tier's placeholder pattern to use for local fallbacks (default: local)",
    )
    parser.add_argument(
        "--local-fallback-placeholder",
        action="append",
        default=[],
        help="Override _local:<category> resolution (e.g. vision=ollama/gemma4:e4b)",
    )
    parser.add_argument(
        "--local-fallback-role",
        action="append",
        default=[],
        help="Override local model for a role (e.g. observer=ollama/qwen3.5:9b-mlx)",
    )

    args = parser.parse_args()

    # Resolve which steps to run
    if args.steps is not None:
        requested = [step_name(s) for s in args.steps.split(",") if s.strip()]
    else:
        requested = list(DEFAULT_STEPS)

    # --all-mcps adds the mcps step
    if args.all_mcps and "mcps" not in requested:
        requested.append("mcps")

    # opencode is always included (step 1 is the core config generation)
    if "opencode" not in requested:
        requested.insert(0, "opencode")

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

    logger.info(f"Steps: {', '.join(requested)}")

    # Step 1: Generate project opencode.json in a temp dir, then move to project root
    if "opencode" in requested:
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
    if "tier" in requested:
        os.makedirs(dotopencode_dir, exist_ok=True)
        opencode_tier_py = os.path.join(SCRIPT_DIR, "configure-opencode-tier.py")

        tier_args_list = build_tier_args(
            tier=args.preset,
            no_local_fallbacks=(
                os.environ.get("DOTFILES_USE_LOCAL_OLLAMA", "1") != "1"
            ),
            local_fallback_preset=args.local_fallback_preset,
            local_fallback_placeholders=args.local_fallback_placeholder or None,
            local_fallback_roles=args.local_fallback_role or None,
        )
        tier_args = [sys.executable, opencode_tier_py] + tier_args_list

        env = os.environ.copy()
        env["OPENCODE_DIR"] = dotopencode_dir

        logger.info(
            f"Running tier switcher on project dir with preset {args.preset}..."
        )
        subprocess.run(tier_args, env=env)
    else:
        logger.info("Skipping oh-my-opencode-slim preset switching (not in --steps)")

    # Step 3: Initialize CodeGraph for the project
    if "codegraph" in requested:
        codegraph_cmd = shutil.which("codegraph")
        if codegraph_cmd:
            logger.info("Running codegraph init -i for project...")
            res = subprocess.run(
                [codegraph_cmd, "init", "-i", project_root],
                capture_output=True,
                text=True,
            )
            if res.returncode == 0:
                logger.info("CodeGraph initialized for project")
            else:
                logger.warning(
                    f"codegraph init failed: {res.stderr.strip() or res.stdout.strip()}"
                )
        else:
            logger.warning(
                "codegraph not found — install with: npm i -g @colbymchenry/codegraph"
            )
    else:
        logger.info("Skipping CodeGraph initialization (not in --steps)")

    # Step 4: Configure MCPs for other AI platforms
    if "mcps" in requested:
        logger.info("Running configure-mcp-all.py for other AI platforms...")
        mcp_args = ["--mode", "project"]
        if args.mcps:
            mcp_args.extend(["--tools", args.mcps])
        if args.project_mcps:
            mcp_args.extend(["--project-mcps", args.project_mcps])

        configure_mcp_all_py = os.path.join(SCRIPT_DIR, "configure-mcp-all.py")
        subprocess.run([sys.executable, configure_mcp_all_py] + mcp_args)
        logger.info("MCPs for other AI platforms configured")
    else:
        logger.info("Skipping MCP configuration for other AI tools (not in --steps)")

    # Summary
    summary_lines = [
        "Project OpenCode config written!",
        "",
        "  Config locations:",
        f"    • opencode.json               → {project_root}/opencode.json",
    ]
    if "tier" in requested:
        summary_lines.append(
            f"    • oh-my-opencode-slim.json     → {dotopencode_dir}/oh-my-opencode-slim.json"
        )
    if "codegraph" in requested:
        summary_lines.append(
            f"    • .codegraph/                  → {project_root}/.codegraph/"
        )
    summary_lines.extend(
        [
            "",
            "  Note: Project config EXTENDS the global config.",
            "  Global config: ~/.config/opencode/",
            "",
            "Project configure complete!",
        ]
    )
    logger.info("\n".join(summary_lines))


if __name__ == "__main__":
    main()
