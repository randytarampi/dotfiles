#!/usr/bin/env python3
"""Distribute home-level agent guidance to all AI agent instruction files.

Reads configs/agents/home-agents.md from the dotfiles repo and distributes its
AGENT_GUIDANCE_START/END section to each agent instruction file. Also writes
~/AGENTS.md as a convenience copy.

Usage:
    configure-agent-guidance.py [--source PATH] [--dry-run] [--force] [--check]

Options:
    --source PATH   Path to home-agents.md (default: configs/agents/home-agents.md in dotfiles repo)
    --dry-run       Show what would change without writing files
    --force         Create missing agent files or overwrite unmarked files
    --check         Audit drift without writing (exit 1 if drift found)
"""

import argparse
import os
import re
import sys

SELF = os.path.realpath(__file__)
SCRIPT_DIR = os.path.dirname(SELF)
DOTFILES_DIR = os.path.dirname(SCRIPT_DIR)
LIB_DIR = os.path.join(SCRIPT_DIR, "lib")

if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger  # noqa: E402
from file_utils import backup_file, write_text_file  # noqa: E402

# Resolve symlinks: ~/.junie may be a symlink to ~/.ai
HOME = os.path.expanduser("~")
JUNIE_DIR = os.path.realpath(os.path.join(HOME, ".junie"))

AGENT_FILES = [
    os.path.join(HOME, ".claude", "CLAUDE.md"),
    os.path.join(HOME, ".gemini", "GEMINI.md"),
    os.path.join(HOME, ".codex", "AGENTS.md"),
    os.path.join(HOME, ".config", "opencode", "AGENTS.md"),
    os.path.join(HOME, ".cursor", "AGENTS.md"),
    os.path.join(JUNIE_DIR, "AGENTS.md"),
    os.path.join(HOME, ".copilot", "copilot-instructions.md"),
]

MARKER_START = "<!-- AGENT_GUIDANCE_START -->"
MARKER_END = "<!-- AGENT_GUIDANCE_END -->"
HEADER_COMMENT = "<!-- Managed by configure-agent-guidance.py — do not edit between AGENT_GUIDANCE markers -->"


def extract_guidance(source_path):
    """Extract the AGENT_GUIDANCE section from the source AGENTS.md."""
    with open(source_path, encoding="utf-8") as f:
        text = f.read()

    start_idx = text.find(MARKER_START)
    if start_idx == -1:
        logger.error(f"Marker {MARKER_START} not found in {source_path}")
        sys.exit(1)

    end_idx = text.find(MARKER_END, start_idx)
    if end_idx == -1:
        logger.error(f"Marker {MARKER_END} not found in {source_path}")
        sys.exit(1)

    # Extract content between markers (inclusive of markers)
    guidance = text[start_idx : end_idx + len(MARKER_END)]
    return guidance


def resolve_symlink_path(path):
    """If path is a symlink, resolve it. Remove broken symlinks."""
    if os.path.islink(path):
        target = os.path.realpath(path)
        if not os.path.exists(target):
            # Broken symlink — remove it
            logger.info(f"Removing broken symlink: {path} -> {target}")
            os.remove(path)
            return path  # Return the original path (now gone)
        return path  # Keep symlink as-is (it resolves)
    return path


def inject(agent_path, guidance, dry_run=False, force=False, check=False):
    """Inject guidance into an agent file.

    Handles:
    - Broken symlinks (removes them, creates real file)
    - Files with AGENT_GUIDANCE markers (replace block)
    - Files without markers (skip unless --force)
    - Missing files (skip unless --force)
    """
    # Handle broken symlink at the file path
    if os.path.islink(agent_path):
        target = os.path.realpath(agent_path)
        if not os.path.exists(target):
            if check:
                logger.error(
                    f"BROKEN SYMLINK: {agent_path} -> {target} (would remove and replace with --force)"
                )
                return False
            if dry_run:
                logger.info(
                    f"[DRY RUN] Would remove broken symlink: {agent_path} -> {target}"
                )
            else:
                logger.info(f"Removing broken symlink: {agent_path} -> {target}")
                os.remove(agent_path)
            # Fall through to create new file
        else:
            # Working symlink — resolve and use the target
            pass

    # Build the new guidance block (header + markers)
    new_block = f"{HEADER_COMMENT}\n{guidance}"

    # File doesn't exist
    if not os.path.exists(agent_path):
        if force:
            parent = os.path.dirname(agent_path)
            if not os.path.isdir(parent):
                if dry_run:
                    logger.info(f"[DRY RUN] Would create directory: {parent}")
                else:
                    os.makedirs(parent, exist_ok=True)
                    logger.info(f"Created directory: {parent}")
            content = f"{new_block}\n"
            if check:
                logger.error(f"MISSING: {agent_path} (would create with --force)")
                return False
            if dry_run:
                logger.info(f"[DRY RUN] Would create: {agent_path}")
                return True
            write_text_file(agent_path, content, backup=False)
            logger.info(f"Created: {agent_path}")
            return True
        logger.warning(f"Not found (use --force to create): {agent_path}")
        return False

    # Read existing content
    with open(agent_path, encoding="utf-8") as f:
        content = f.read()

    # Check for new markers
    if MARKER_START in content and MARKER_END in content:
        # Find the replacement range: include header comment line if present
        start_idx = content.find(MARKER_START)
        # Check if header comment precedes the marker (with optional newline)
        header_prefix = HEADER_COMMENT + "\n"
        if (
            start_idx >= len(header_prefix)
            and content[start_idx - len(header_prefix) : start_idx] == header_prefix
        ):
            start_idx -= len(header_prefix)
        end_idx = content.find(MARKER_END) + len(MARKER_END)
        old_block = content[start_idx:end_idx]

        if old_block == new_block:
            logger.info(f"Already up to date: {agent_path}")
            return True

        if check:
            logger.error(f"DRIFT: {agent_path} (markers exist but content differs)")
            return False

        new_content = content[:start_idx] + new_block + content[end_idx:]

        if dry_run:
            logger.info(f"[DRY RUN] Would update: {agent_path}")
            return True

        backup_file(agent_path, enabled=True)
        write_text_file(agent_path, new_content, backup=False)
        logger.info(f"Updated: {agent_path}")
        return True

    # No markers found
    if force:
        if check:
            logger.error(
                f"MISSING: {agent_path} (no markers, would append with --force)"
            )
            return False

        new_content = content.rstrip("\n") + "\n\n" + new_block + "\n"

        if dry_run:
            logger.info(f"[DRY RUN] Would append guidance to: {agent_path}")
            return True

        backup_file(agent_path, enabled=True)
        write_text_file(agent_path, new_content, backup=False)
        logger.info(f"Appended guidance to: {agent_path}")
        return True

    logger.warning(f"No markers in {agent_path} (use --force to append)")
    return False


def main():
    parser = argparse.ArgumentParser(
        description="Distribute home-level agent guidance to all AI agent instruction files"
    )
    parser.add_argument(
        "--source",
        default=os.path.join(DOTFILES_DIR, "configs", "agents", "home-agents.md"),
        help="Path to home-agents.md (default: configs/agents/home-agents.md in dotfiles repo)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing files",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Create missing agent files or append to files without markers",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Audit drift without writing (exit 1 if drift found)",
    )
    args = parser.parse_args()

    source_path = args.source
    if not os.path.exists(source_path):
        logger.error(f"Source file not found: {source_path}")
        logger.error("Expected configs/agents/home-agents.md in the dotfiles repo")
        sys.exit(1)

    guidance = extract_guidance(source_path)

    # Write ~/AGENTS.md as a convenience copy of the home-level guidance
    home_agents = os.path.join(HOME, "AGENTS.md")
    with open(source_path, encoding="utf-8") as f:
        source_content = f.read()

    if not args.check:
        if args.dry_run:
            logger.info(f"[DRY RUN] Would write {home_agents}")
        else:
            write_text_file(home_agents, source_content)
            logger.info(f"Wrote {home_agents}")

    updated = 0
    drift = False

    for path in AGENT_FILES:
        result = inject(
            path, guidance, dry_run=args.dry_run, force=args.force, check=args.check
        )
        if result:
            updated += 1
        elif args.check:
            drift = True

    total = len(AGENT_FILES)
    if args.check:
        if drift:
            logger.error(f"Drift detected in {total - updated}/{total} agent files")
            sys.exit(1)
        logger.info(f"All {updated}/{total} agent files are up to date")
    elif args.dry_run:
        logger.info(
            f"[DRY RUN] Agent guidance would be injected into {updated}/{total} agent files"
        )
    else:
        logger.info(f"Agent guidance injected into {updated}/{total} agent files")


if __name__ == "__main__":
    main()
