#!/usr/bin/env python3
"""Configure project-scoped OpenCode and AI tooling."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = os.path.join(SCRIPT_DIR, "lib")
sys.path.insert(0, LIB_DIR)

import logger
from env import load_env
from skills_manifest import CANONICAL_STORE, load_manifest, symlink_skill_to_targets
from opencode_config import build_tier_args
from cli_helpers import (
    add_common_args,
    forward_common_args,
    forward_local_fallback_args,
)

ALL_STEPS = [
    "opencode",
    "tier",
    "codegraph",
    "mcps",
    "skills",
    "jetbrains",
    "junie",
    "acp-agents",
    "secrets",
]
DEFAULT_STEPS = ["opencode", "tier"]
MANIFEST_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "configs", "skills")


def csv(value):
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def is_truthy(value):
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def resolve_steps(args):
    """Resolve project steps, giving explicit step lists precedence."""
    if args.steps is not None:
        return csv(args.steps)
    if "DOTFILES_PROJECT_STEPS" in os.environ:
        return csv(os.environ["DOTFILES_PROJECT_STEPS"])

    steps = list(DEFAULT_STEPS)
    if is_truthy(os.environ.get("DOTFILES_PROJECT_CODEGRAPH")):
        steps.append("codegraph")
    if os.environ.get("DOTFILES_PROJECT_MCPS") or os.environ.get(
        "DOTFILES_PROJECT_MCP_TOOLS"
    ):
        steps.append("mcps")
    if os.environ.get("DOTFILES_PROJECT_SKILL_PROFILES") or os.environ.get(
        "DOTFILES_PROJECT_SKILLS"
    ):
        steps.append("skills")
    if is_truthy(os.environ.get("DOTFILES_PROJECT_JETBRAINS")):
        steps.append("jetbrains")
    if is_truthy(os.environ.get("DOTFILES_PROJECT_JUNIE")):
        steps.append("junie")
    if os.environ.get("DOTFILES_PROJECT_ACP_AGENTS"):
        steps.append("acp-agents")
    if is_truthy(os.environ.get("DOTFILES_PROJECT_SECRETS")):
        steps.append("secrets")
    return steps


def run(command, cwd, env=None):
    logger.info("Running: " + " ".join(command))
    result = subprocess.run(command, cwd=cwd, env=env)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(command)}")


def env_or(args_value, env_name, default=""):
    return args_value if args_value is not None else os.environ.get(env_name, default)


def configure_skills(root, profiles, extras, skipped):
    manifest = load_manifest(MANIFEST_DIR)
    # Project mode bypasses profile gates — the project's .env declares which
    # profiles it wants via DOTFILES_PROJECT_SKILL_PROFILES, and we resolve them
    # directly from the manifest without filtering through env-var gates.
    entries = [
        (profile_name, skill)
        for profile_name, profile_data in manifest.get("profiles", {}).items()
        if profile_name in profiles
        for skill in profile_data.get("skills", [])
    ]
    by_name = {
        skill["name"]: skill
        for _, data in manifest.get("profiles", {}).items()
        for skill in data.get("skills", [])
    }
    for name in manifest.get("preinstalled", {}).get("skills", []):
        by_name.setdefault(name, {"name": name, "source": "preinstalled"})
    selected = {skill["name"] for profile, skill in entries}
    for name in extras:
        if name not in by_name:
            raise RuntimeError(f"Skill is not present in the manifest: {name}")
        selected.add(name)
    selected.difference_update(skipped)

    target = Path(root) / ".opencode" / "skills"
    target.mkdir(parents=True, exist_ok=True)
    for child in target.iterdir():
        if child.is_symlink() and child.name not in selected:
            child.unlink()
    for name in sorted(selected):
        symlink_skill_to_targets(
            name,
            canonical_store=CANONICAL_STORE,
            target_dirs=[str(target)],
        )

    opencode_path = Path(root) / "opencode.json"
    config_paths = [opencode_path, Path(root) / ".opencode" / "opencode.json"]
    if skipped and not any(path.exists() for path in config_paths):
        config_paths = [config_paths[-1]]
        config_paths[0].parent.mkdir(parents=True, exist_ok=True)
        config_paths[0].write_text("{}\n", encoding="utf-8")
    for config_path in config_paths:
        if not skipped or not config_path.exists():
            continue
        with config_path.open(encoding="utf-8") as handle:
            config = json.load(handle)
        permissions = config.setdefault("permission", {})
        skill_permissions = permissions.setdefault("skill", {})
        for name in skipped:
            skill_permissions[name] = "deny"
        with config_path.open("w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
            handle.write("\n")


def main():
    parser = argparse.ArgumentParser(description="Configure project-scoped AI tooling.")
    add_common_args(parser)
    parser.add_argument("--preset", default=None)
    parser.add_argument(
        "--steps",
        default=None,
        help=f"Comma-separated steps (default: {','.join(DEFAULT_STEPS)})",
    )
    parser.add_argument("--workspace-root", default=os.getcwd())
    parser.add_argument("--skill-profiles", default=None)
    parser.add_argument("--skills", default=None)
    parser.add_argument("--skip-skills", default=None)
    parser.add_argument("--mcps", default=None)
    parser.add_argument("--mcp-tools", default=None)
    parser.add_argument("--acp-agents", default=None)
    parser.add_argument("--junie-dirs", action="store_true")
    parser.add_argument("--junie-mcp", action="store_true")
    parser.add_argument("--local-fallback-preset", default=None)
    parser.add_argument("--local-fallback-placeholder", action="append", default=[])
    parser.add_argument("--local-fallback-role", action="append", default=[])
    args = parser.parse_args()
    root = os.path.abspath(args.workspace_root)
    opencode_dir = os.path.join(root, ".opencode")
    load_env(os.path.expanduser("~/.env"))
    load_env(os.path.join(opencode_dir, ".env"))
    load_env(os.path.join(opencode_dir, ".env.local"))
    steps = resolve_steps(args)
    unknown = set(steps) - set(ALL_STEPS)
    if unknown:
        parser.error(f"Unknown step(s): {', '.join(sorted(unknown))}")
    preset = env_or(args.preset, "DOTFILES_PROJECT_PRESET", "pro-plus")
    child_env = os.environ.copy()
    if args.dry_run:
        logger.info(f"Would ensure project OpenCode directory exists: {opencode_dir}")
    else:
        os.makedirs(opencode_dir, exist_ok=True)

    if "opencode" in steps:
        temp = os.path.join(opencode_dir, ".tmp-opencode")
        os.makedirs(temp, exist_ok=True)
        env = child_env.copy()
        env["OPENCODE_DIR"] = temp
        run(
            [
                sys.executable,
                os.path.join(SCRIPT_DIR, "configure-opencode.py"),
                "--mode",
                "project",
                "--preset",
                preset,
            ]
            + forward_common_args(args)
            + forward_local_fallback_args(args),
            root,
            env,
        )
        if args.dry_run:
            logger.info(f"Would copy generated project config to {root}/opencode.json")
        else:
            generated = os.path.join(temp, "opencode.json")
            if not os.path.isfile(generated):
                raise RuntimeError(
                    "configure-opencode.py did not generate opencode.json"
                )
            shutil.copy(generated, os.path.join(root, "opencode.json"))
            shutil.rmtree(temp, ignore_errors=True)
    if "tier" in steps:
        if args.dry_run:
            logger.info("Would set project tier (skipped: child has no --dry-run)")
        else:
            env = child_env.copy()
            env["OPENCODE_DIR"] = opencode_dir
            run(
                [sys.executable, os.path.join(SCRIPT_DIR, "configure-opencode-tier.py")]
                + build_tier_args(
                    tier=preset,
                    local_fallback_preset=args.local_fallback_preset,
                    local_fallback_placeholders=args.local_fallback_placeholder or None,
                    local_fallback_roles=args.local_fallback_role or None,
                ),
                root,
                env,
            )
    if "codegraph" in steps:
        command = shutil.which("codegraph")
        if not command:
            raise RuntimeError("codegraph not found")
        run([command, "init", "-i"], root, child_env)
    if "mcps" in steps:
        command = [
            sys.executable,
            os.path.join(SCRIPT_DIR, "configure-mcps.py"),
            "--mode",
            "project",
        ]
        tools = env_or(args.mcp_tools, "DOTFILES_PROJECT_MCP_TOOLS")
        mcps = env_or(args.mcps, "DOTFILES_PROJECT_MCPS")
        if tools:
            command += ["--tools", tools]
        if mcps:
            command += ["--project-mcps", mcps]
        if args.dry_run:
            command.append("--dry-run")
        run(command, root, child_env)
    if "skills" in steps:
        if args.dry_run:
            logger.info(
                "Would reconcile project skills (skipped: helper has no dry-run API)"
            )
        else:
            configure_skills(
                root,
                csv(env_or(args.skill_profiles, "DOTFILES_PROJECT_SKILL_PROFILES")),
                csv(env_or(args.skills, "DOTFILES_PROJECT_SKILLS")),
                csv(env_or(args.skip_skills, "DOTFILES_PROJECT_SKIP_SKILLS")),
            )
    if "jetbrains" in steps:
        env = child_env.copy()
        env["PROJECT_CONFIG_DELEGATE"] = "1"
        if args.dry_run:
            logger.info(
                "Would configure JetBrains workspace "
                "(skipped: child has no --dry-run)"
            )
        else:
            run(
                [
                    sys.executable,
                    os.path.join(
                        SCRIPT_DIR, "configure-jetbrains-workspace-project.py"
                    ),
                    "--workspace-root",
                    root,
                ],
                root,
                env,
            )
    if "junie" in steps:
        command = [
            sys.executable,
            os.path.join(SCRIPT_DIR, "configure-jetbrains-ai.py"),
            "--project-dir",
            root,
        ]
        if args.junie_dirs:
            command.append("--dirs")
        if args.junie_mcp:
            command.append("--mcp")
        command.append("--all-tools")
        command += forward_common_args(args)
        # TODO: Junie local-fallback support is deferred until
        # generate-jetbrains-profiles.py accepts these arguments.
        run(command, root, child_env)
    if "acp-agents" in steps:
        agents = env_or(args.acp_agents, "DOTFILES_PROJECT_ACP_AGENTS")
        command = [
            sys.executable,
            os.path.join(SCRIPT_DIR, "configure-acp-agents.py"),
            "--output",
            os.path.join(opencode_dir, "acp-agents.json"),
            "--slim-file",
            os.path.join(opencode_dir, "oh-my-opencode-slim.json"),
        ]
        if agents:
            command += ["--agents", agents]
        if args.dry_run:
            logger.info(
                "Would configure project ACP agents (skipped: child has no --dry-run)"
            )
        else:
            run(command, root, child_env)
    if "secrets" in steps:
        if args.dry_run:
            logger.info(
                "Would generate project secrets (skipped: child has no --dry-run)"
            )
        else:
            run(
                [
                    sys.executable,
                    os.path.join(SCRIPT_DIR, "configure-secrets.py"),
                    "--mode",
                    "project",
                    "--output",
                    os.path.join(opencode_dir, ".env.local"),
                ],
                root,
                child_env,
            )


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, json.JSONDecodeError) as exc:
        logger.critical(str(exc))
        raise SystemExit(1)
