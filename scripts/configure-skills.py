#!/usr/bin/env python3
"""Distribute skills from configs/skills/ to all configured agent skill directories.

Scans configs/skills/ for skill directories (each containing a SKILL.md) and
copies each SKILL.md to all agent skill directories:
  - ~/.agents/skills/<skill-name>/SKILL.md (shared)
  - ~/.config/opencode/skills/<skill-name>/SKILL.md (OpenCode)
  - ~/.claude/skills/<skill-name>/SKILL.md (Claude Code)
  - ~/.gemini/skills/<skill-name>/SKILL.md (Gemini)
  - ~/.codex/skills/<skill-name>/SKILL.md (Codex)
  - ~/.cursor/skills/<skill-name>/SKILL.md (Cursor)
  - ~/.ai/skills/<skill-name>/SKILL.md (general AI / Junie via symlink)

Usage:
    configure-skills.py [--dry-run]

Options:
    --dry-run       Show what would change without writing files
"""

import argparse
import os
import sys

SELF = os.path.realpath(__file__)
SCRIPT_DIR = os.path.dirname(SELF)
DOTFILES_DIR = os.path.dirname(SCRIPT_DIR)
LIB_DIR = os.path.join(SCRIPT_DIR, "lib")

if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger  # noqa: E402
from file_utils import write_text_file  # noqa: E402

HOME = os.path.expanduser("~")
JUNIE_DIR = os.path.realpath(os.path.join(HOME, ".junie"))

SKILL_TARGET_DIRS = [
    os.path.join(HOME, ".agents", "skills"),
    os.path.join(HOME, ".config", "opencode", "skills"),
    os.path.join(HOME, ".claude", "skills"),
    os.path.join(HOME, ".gemini", "skills"),
    os.path.join(HOME, ".codex", "skills"),
    os.path.join(HOME, ".cursor", "skills"),
    os.path.join(JUNIE_DIR, "skills"),
]

SKILLS_DIR = os.path.join(DOTFILES_DIR, "configs", "skills")


def discover_skills(skills_dir):
    """Scan skills_dir for skill directories containing SKILL.md.

    Returns a list of (skill_name, source_path) tuples.
    """
    skills = []
    if not os.path.isdir(skills_dir):
        logger.warning(f"Skills directory not found: {skills_dir}")
        return skills

    for entry in sorted(os.listdir(skills_dir)):
        skill_dir = os.path.join(skills_dir, entry)
        if os.path.isdir(skill_dir):
            skill_md = os.path.join(skill_dir, "SKILL.md")
            if os.path.isfile(skill_md):
                skills.append((entry, skill_md))

    return skills


def distribute_skill(skill_name, source_path, dry_run=False):
    """Copy a SKILL.md to all target directories.

    Returns the number of targets written.
    """
    with open(source_path, "r", encoding="utf-8") as f:
        content = f.read()

    written = 0
    for target_base in SKILL_TARGET_DIRS:
        target_path = os.path.join(target_base, skill_name, "SKILL.md")
        target_dir = os.path.dirname(target_path)

        if dry_run:
            logger.info(f"[DRY RUN] Would write: {target_path}")
            written += 1
            continue

        os.makedirs(target_dir, exist_ok=True)
        write_text_file(target_path, content, backup=False)
        logger.info(f"Wrote: {target_path}")
        written += 1

    return written


def main():
    parser = argparse.ArgumentParser(
        description="Distribute skills from configs/skills/ to all agent skill directories"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing files",
    )
    args = parser.parse_args()

    # Gate on DOTFILES_RUN_SKILLS_SETUP env var
    if os.environ.get("DOTFILES_RUN_SKILLS_SETUP", "0") != "1":
        logger.info(
            "DOTFILES_RUN_SKILLS_SETUP='%s' — skipping skills distribution",
            os.environ.get("DOTFILES_RUN_SKILLS_SETUP", "0"),
        )
        return

    skills = discover_skills(SKILLS_DIR)

    if not skills:
        logger.info("No skills found in %s", SKILLS_DIR)
        return

    total_targets = len(SKILL_TARGET_DIRS)
    total_written = 0
    skill_count = len(skills)

    for skill_name, source_path in skills:
        written = distribute_skill(skill_name, source_path, dry_run=args.dry_run)
        total_written += written

    if args.dry_run:
        logger.info(
            "[DRY RUN] Would distribute %d skill(s) to %d target(s) each (%d total writes)",
            skill_count,
            total_targets,
            total_written,
        )
    else:
        logger.info(
            "Distributed %d skill(s) to %d target(s) each (%d total writes)",
            skill_count,
            total_targets,
            total_written,
        )


if __name__ == "__main__":
    main()
