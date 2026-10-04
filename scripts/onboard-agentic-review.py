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

SOURCE = SCRIPT_DIR.parent / "configs" / "review" / "dispatcher-stub.yml"
COPILOT_SOURCE = SCRIPT_DIR.parent / ".github" / "workflows" / "copilot-setup-steps.yml"
SKILL_SOURCE = SCRIPT_DIR.parent / ".github" / "skills" / "code-review" / "SKILL.md"


def build_workflow(ref):
    content = SOURCE.read_text(encoding="utf-8")
    return content.replace("__REF__", ref)


_COPILOT_DEFAULT_RE = re.compile(r"(?m)^[ \t]*default:[ \t]*([0-9a-f]{40})[ \t]*$")
_COPILOT_FALLBACK_RE = re.compile(
    r"TRUSTED_REF:\s*\$\{\{\s*inputs\.trusted_ref\s*\|\|\s*'([0-9a-f]{40})'\s*\}\}"
)


def pinned_copilot_trusted_ref(content):
    """Return the pinned SHA after checking default and automatic-run fallback match."""
    defaults = list(_COPILOT_DEFAULT_RE.finditer(content))
    if len(defaults) != 1:
        raise ValueError(
            "Copilot setup workflow must contain one pinned trusted_ref default"
        )
    trusted_ref = defaults[0].group(1)
    fallbacks = list(_COPILOT_FALLBACK_RE.finditer(content))
    if len(fallbacks) != 1 or fallbacks[0].group(1) != trusted_ref:
        raise ValueError(
            "Copilot setup workflow must use its pinned trusted_ref default for automatic runs"
        )
    if content.count(trusted_ref) != 2:
        raise ValueError(
            "Copilot setup workflow must use its pinned trusted_ref default for automatic runs"
        )
    return trusted_ref


def build_copilot_workflow(ref):
    content = COPILOT_SOURCE.read_text(encoding="utf-8")
    trusted_ref = pinned_copilot_trusted_ref(content)
    return content.replace(trusted_ref, ref)


def checklist():
    return """Agentic review onboarding checklist:

Secrets:
  OPENCODE_API_KEY, JUNIE_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY
Labels:
  review-opencode, review-junie, review-gemini, review-copilot, review-all
Copilot setup:
  Automatic setup uses the immutable trusted SHA baked into the workflow.
  Manual workflow_dispatch can optionally override it with a trusted dotfiles SHA.
  In Settings → Copilot → MCP servers, add the read-only local codegraph server:
    codegraph serve --mcp
  Use the COPILOT_MCP_* prefix for Copilot MCP secrets.
Shared assets:
  The shared prompt and skills live in randytarampi/dotfiles and are checked
  out automatically by the reusable workflow; nothing extra is needed locally.
  The code-review skill is installed to .github/skills/ (read natively by
  Copilot code review; copied into .opencode/skills/ for the OpenCode lane).
Dispatcher:
  The installed dispatcher is a stable stub. Pass the latest trusted workflow
  commit with --ref; the generated output uses that same immutable SHA for the
  reusable-workflow pin and Copilot setup default. Re-onboarding is only needed
  when trigger events or permissions change, or when advancing the trusted SHA.
Fix lane:
  agentic-review-fix.yml is intentionally dotfiles-only and is not distributed
  to downstream repositories. It requires owner authentication and the
  agentic-review-fix environment approval before publishing a draft PR. Its
  base_sha must be an existing 40-hex commit, and allowed_paths must list every
  exact file permitted to change (one path per line); each run publishes a
  unique agentic-review-bot/<run-id> branch. The generator installs and verifies
  the pinned OpenCode CLI (opencode-ai@1.18.34); reruns use an exact-ref
  force-with-lease guarded by the previously observed remote tip.
Usage:
  Mention plus text requests an ad-hoc task; review labels request the standard review.
  Supported mentions: /oc, /opencode, @oc, @opencode, @junie-agent, @junie,
  @gemini-cli, @gemini, and @copilot.
"""


def main():
    parser = argparse.ArgumentParser(
        description="Onboard a repository to the dotfiles agentic-review dispatcher",
        allow_abbrev=False,
    )
    add_common_args(parser, no_backup=True)
    parser.add_argument("--repo", required=True, help="Target repository root")
    parser.add_argument(
        "--ref",
        required=True,
        help="Immutable 40-hex dotfiles commit used by the dispatcher and Copilot setup",
    )
    parser.add_argument(
        "--workflows-only",
        action="store_true",
        help="Skip printing the onboarding checklist",
    )
    args = parser.parse_args()

    if not re.fullmatch(r"[0-9a-f]{40}", args.ref):
        parser.error("--ref must be a 40-character lowercase hexadecimal commit SHA")
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
        copilot_content = build_copilot_workflow(args.ref)
    except (OSError, ValueError) as exc:
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
