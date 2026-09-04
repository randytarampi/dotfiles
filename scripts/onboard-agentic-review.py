#!/usr/bin/env python3
"""Install the reusable agentic-review dispatcher in another repository."""

import argparse
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))

import logger
from cli_helpers import add_common_args
from file_utils import backup_file

SOURCE = SCRIPT_DIR.parent / ".github" / "workflows" / "agent-review.yml"
COPILOT_SOURCE = SCRIPT_DIR.parent / ".github" / "workflows" / "copilot-setup-steps.yml"
SKILL_SOURCE = SCRIPT_DIR.parent / ".github" / "skills" / "code-review" / "SKILL.md"
LOCAL_WORKFLOW = "uses: ./.github/workflows/agentic-review.yml"


def build_workflow(ref):
    content = SOURCE.read_text(encoding="utf-8")
    replacement = (
        "uses: randytarampi/dotfiles/.github/workflows/agentic-review.yml@" f"{ref}"
    )
    updated, count = re.subn(
        rf"^([ \t]*){re.escape(LOCAL_WORKFLOW)}\s*$",
        rf"\1{replacement}",
        content,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise RuntimeError(
            f"Expected exactly one local reusable-workflow reference, found {count}"
        )
    return updated


def checklist():
    return """Agentic review onboarding checklist:

Secrets:
  OPENCODE_API_KEY, JUNIE_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY
Labels:
  review-opencode, review-junie, review-gemini, review-copilot, review-all
Copilot setup:
  Run copilot-setup-steps.yml once via workflow_dispatch.
  In Settings → Copilot → MCP servers, add the read-only local codegraph server:
    codegraph serve --mcp
  Use the COPILOT_MCP_* prefix for Copilot MCP secrets.
Shared assets:
  The shared prompt and skills live in randytarampi/dotfiles and are checked
  out automatically by the reusable workflow; nothing extra is needed locally.
  The code-review skill is installed to .github/skills/ (read natively by
  Copilot code review; copied into .opencode/skills/ for the OpenCode lane).
Usage:
  Mention plus text requests an ad-hoc task; review labels request the standard review.
  Supported mentions: /oc, /opencode, @junie-agent, and @gemini-cli.
"""


def main():
    parser = argparse.ArgumentParser(
        description="Onboard a repository to the dotfiles agentic-review dispatcher",
        allow_abbrev=False,
    )
    add_common_args(parser, no_backup=True)
    parser.add_argument("--repo", required=True, help="Target repository root")
    parser.add_argument(
        "--ref", default="main", help="Dotfiles ref used by the reusable workflow"
    )
    parser.add_argument(
        "--workflows-only",
        action="store_true",
        help="Skip printing the onboarding checklist",
    )
    args = parser.parse_args()

    if not args.ref or any(char in args.ref for char in "\r\n"):
        parser.error("--ref must be a non-empty single-line value")
    if not SOURCE.is_file():
        logger.critical(f"Dispatcher source does not exist: {SOURCE}")
        return 1

    target = Path(args.repo).expanduser().resolve()
    if not target.is_dir() or not (target / ".git").exists():
        parser.error("--repo must be an existing repository root containing .git")
    destination = target / ".github" / "workflows" / "agent-review.yml"
    copilot_destination = target / ".github" / "workflows" / "copilot-setup-steps.yml"
    try:
        content = build_workflow(args.ref)
    except (OSError, RuntimeError) as exc:
        logger.critical(f"Could not prepare dispatcher: {exc}")
        return 1

    try:
        copilot_content = COPILOT_SOURCE.read_text(encoding="utf-8")
    except OSError as exc:
        logger.critical(f"Could not read Copilot setup workflow: {exc}")
        return 1

    try:
        skill_content = SKILL_SOURCE.read_text(encoding="utf-8")
    except OSError as exc:
        logger.critical(f"Could not read code-review skill: {exc}")
        return 1

    skill_destination = target / ".github" / "skills" / "code-review" / "SKILL.md"

    for destination, workflow in (
        (destination, content),
        (copilot_destination, copilot_content),
        (skill_destination, skill_content),
    ):
        if (
            destination.is_file()
            and destination.read_text(encoding="utf-8") == workflow
        ):
            logger.info(f"Workflow unchanged: {destination}")
        elif args.dry_run:
            logger.info(f"[DRY RUN] Would copy workflow to {destination}")
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() and not args.no_backup:
                backup_file(str(destination), enabled=True)
            destination.write_text(workflow, encoding="utf-8")
            logger.info(f"Installed workflow: {destination}")

    if not args.workflows_only:
        logger.info(checklist())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
