#!/usr/bin/env python3
"""Reconcile installed skills against the declarative manifest.

Reads configs/skills/skills.json and category files, fetches missing skills via
the `skills` CLI into ~/.local/share/dotfiles/skills/ (canonical store), symlinks them to all agent skill dirs,
and removes stale skills not in the active manifest.

Agent target dirs (symlink targets):
  - ~/.agents/skills/<skill-name>/          (canonical store, not symlinked to itself)
  - ~/.config/opencode/skills/<skill-name>/  (OpenCode)
  - ~/.claude/skills/<skill-name>/           (Claude Code)
  - ~/.codex/skills/<skill-name>/            (Codex)
  - ~/.cursor/skills/<skill-name>/           (Cursor)
  - ~/.ai/skills/<skill-name>/               (general AI / Junie via symlink)

Usage:
    configure-skills.py [--dry-run] [--update]

Options:
    --dry-run       Show what would change without writing files
    --update        Update all skills from upstream before reconciling
"""

import argparse
import os
import shutil
import subprocess
import sys

SELF = os.path.realpath(__file__)
SCRIPT_DIR = os.path.dirname(SELF)
DOTFILES_DIR = os.path.dirname(SCRIPT_DIR)
LIB_DIR = os.path.join(SCRIPT_DIR, "lib")

if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger  # noqa: E402
from skills_manifest import (  # noqa: E402
    CANONICAL_STORE,
    SKILL_TARGET_DIRS,
    fetch_skill_via_cli,
    find_missing_skills,
    find_stale_skills,
    get_active_skill_names,
    get_catalog_skills,
    get_globally_active_skills,
    get_symlinked_skills,
    get_installed_skills,
    get_preinstalled_skills,
    install_repo_local_skill,
    remove_stale_from_canonical_store,
    remove_stale_from_lock_file,
    remove_stale_from_targets,
    remove_stale_via_cli,
    symlink_skill_to_targets,
    verify_reconciliation,
)

MANIFEST_PATH = os.path.join(DOTFILES_DIR, "configs", "skills", "skills.json")
REPO_LOCAL_SKILLS_DIR = os.path.join(DOTFILES_DIR, "configs", "skills")
LEGACY_CANONICAL_STORE = os.path.join(os.path.expanduser("~"), ".agents", "skills")


def migrate_legacy_canonical_store(dry_run: bool = False) -> None:
    """Migrate declared skills from the former discovery-dir store."""
    if not os.path.isdir(LEGACY_CANONICAL_STORE):
        return
    logger.info(
        "Migrating legacy skills store: %s -> %s",
        LEGACY_CANONICAL_STORE,
        CANONICAL_STORE,
    )
    catalog = {entry.name for entry in get_catalog_skills(MANIFEST_PATH)}
    if dry_run:
        for name in sorted(catalog):
            if not os.path.isdir(os.path.join(CANONICAL_STORE, name)):
                logger.info("[DRY RUN] Would migrate/fetch catalog skill: %s", name)
        return
    try:
        os.makedirs(CANONICAL_STORE, exist_ok=True)
        for name in sorted(catalog):
            source = os.path.join(LEGACY_CANONICAL_STORE, name)
            target = os.path.join(CANONICAL_STORE, name)
            # Only migrate real legacy directories; symlinks are rebuilt below.
            if (
                not os.path.exists(target)
                and os.path.isdir(source)
                and not os.path.islink(source)
            ):
                shutil.move(source, target)
    except OSError as exc:
        logger.warning("Legacy skills store migration failed: %s", exc)


def update_all_skills(dry_run: bool = False) -> bool:
    """Run `skills update --global -y` to pull latest skill content from upstream.

    Returns True on success (or dry-run), False on failure.
    """
    skills_cli = shutil.which("skills")
    if not skills_cli:
        logger.warning("`skills` CLI not found on PATH — cannot update")
        return False

    if dry_run:
        logger.info("[DRY RUN] Would run: skills update --global -y")
        return True

    try:
        result = subprocess.run(
            [skills_cli, "update", "--global", "-y"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode == 0:
            logger.info("Updated all skills from upstream")
            return True
        else:
            logger.warning(
                "skills update failed: %s",
                result.stderr.strip() or result.stdout.strip(),
            )
            return False
    except subprocess.TimeoutExpired:
        logger.warning("Timeout running skills update")
        return False
    except Exception as exc:
        logger.warning("Error running skills update: %s", exc)
        return False


def discover_repo_local_skills(skills_dir: str) -> dict[str, str]:
    """Scan configs/skills/ for repo-local skill directories containing SKILL.md.

    Returns a dict mapping skill_name -> source_dir.
    Only returns directories that are NOT the manifest file itself.
    """
    skills = {}
    if not os.path.isdir(skills_dir):
        return skills

    for entry in sorted(os.listdir(skills_dir)):
        if entry == "skills.json":
            continue
        skill_dir = os.path.join(skills_dir, entry)
        if os.path.isdir(skill_dir):
            skill_md = os.path.join(skill_dir, "SKILL.md")
            if os.path.isfile(skill_md):
                skills[entry] = skill_dir

    return skills


def main():
    parser = argparse.ArgumentParser(
        description="Reconcile installed skills against the declarative manifest"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing files",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Update all skills from upstream before reconciling",
    )
    args = parser.parse_args()

    # Gate on DOTFILES_RUN_SKILLS_SETUP env var
    if os.environ.get("DOTFILES_RUN_SKILLS_SETUP", "0") != "1":
        logger.info(
            "DOTFILES_RUN_SKILLS_SETUP='%s' — skipping skills distribution",
            os.environ.get("DOTFILES_RUN_SKILLS_SETUP", "0"),
        )
        return

    # Catalog and global activation are deliberately separate: gated skills are
    # cached, but are not exposed through discovery directories.
    migrate_legacy_canonical_store(dry_run=args.dry_run)
    catalog_skills = get_catalog_skills(MANIFEST_PATH)
    active_skills = get_globally_active_skills(MANIFEST_PATH)

    if not catalog_skills:
        logger.warning("No catalog skills found in manifest %s", MANIFEST_PATH)
        return

    # Get preinstalled skills (installed by other means, just symlink them)
    preinstalled_names = get_preinstalled_skills(MANIFEST_PATH)

    # Discover repo-local skills (stored directly in configs/skills/)
    repo_local_dirs = discover_repo_local_skills(REPO_LOCAL_SKILLS_DIR)

    # Add repo-local and preinstalled skills to the active set
    catalog_names = {skill.name for skill in catalog_skills}
    active_names = {skill.name for skill in active_skills}
    for skill_name, source_dir in repo_local_dirs.items():
        if skill_name not in active_names:
            logger.info("Found repo-local skill not in manifest: %s", skill_name)
            active_names.add(skill_name)
    # Preinstalled skills are already in the canonical store — add to active
    # so they get symlinked and protected from stale removal
    active_names |= preinstalled_names

    # Optionally update from upstream first
    if args.update:
        update_all_skills(dry_run=args.dry_run)

    # Get currently installed skills in canonical store
    installed_names = get_installed_skills(CANONICAL_STORE)

    # Find missing and stale skills
    missing = find_missing_skills(catalog_names, installed_names)
    stale = find_stale_skills(catalog_names, installed_names)

    # Fetch missing skills via `skills` CLI
    fetched = 0
    for skill in catalog_skills:
        if skill.name in missing and not skill.is_repo_local:
            if skill.name in preinstalled_names:
                continue
            success = fetch_skill_via_cli(
                skill.source, skill.name, dry_run=args.dry_run
            )
            if success:
                fetched += 1

    # Install repo-local skills (copy full tree into canonical store)
    repo_local_installed = 0
    for skill_name, source_dir in repo_local_dirs.items():
        if skill_name in missing or skill_name in active_names:
            success = install_repo_local_skill(
                skill_name,
                source_dir,
                canonical_store=CANONICAL_STORE,
                dry_run=args.dry_run,
            )
            if success:
                repo_local_installed += 1

    # Re-read installed after fetching
    if not args.dry_run:
        installed_names = get_installed_skills(CANONICAL_STORE)

    # Symlink all active skills to all agent dirs
    symlinked = 0
    for skill_name in sorted(active_names & installed_names):
        count = symlink_skill_to_targets(
            skill_name, canonical_store=CANONICAL_STORE, dry_run=args.dry_run
        )
        symlinked += count

    # Recompute stale based on what's actually installed now. Cache cleanup is
    # manual because the CLI only knows about ~/.agents/skills.
    if not args.dry_run:
        installed_names = get_installed_skills(CANONICAL_STORE)
        stale = find_stale_skills(catalog_names, installed_names)
    removed_cache = remove_stale_from_canonical_store(stale, dry_run=args.dry_run)
    stale_targets = set()
    for target_dir in SKILL_TARGET_DIRS:
        stale_targets |= get_symlinked_skills(target_dir) - active_names
        if os.path.isdir(target_dir):
            stale_targets |= {
                name
                for name in os.listdir(target_dir)
                if os.path.islink(os.path.join(target_dir, name))
                and not os.path.exists(os.path.join(target_dir, name))
            }
    legacy_real_stale = {
        name
        for name in stale_targets
        if os.path.isdir(os.path.join(LEGACY_CANONICAL_STORE, name))
        and not os.path.islink(os.path.join(LEGACY_CANONICAL_STORE, name))
    }
    removed_targets = remove_stale_from_targets(stale_targets, dry_run=args.dry_run)
    removed_cli = remove_stale_via_cli(legacy_real_stale, dry_run=args.dry_run)
    removed_lock = 0
    # Remove every lock entry that is not declared, including entries no longer
    # present on disk (the CLI cannot clean those).
    lock_stale = set()
    lock_path = os.path.join(os.path.expanduser("~"), ".agents", ".skill-lock.json")
    if os.path.isfile(lock_path):
        import json

        with open(lock_path, encoding="utf-8") as f:
            lock_stale = {
                item if isinstance(item, str) else item.get("name")
                for item in json.load(f).get("skills", [])
            } - catalog_names
    removed_lock += remove_stale_from_lock_file(lock_stale, dry_run=args.dry_run)

    # Summary
    summary_lines = [
        f"Skills reconciliation complete!",
        f"  Manifest: {MANIFEST_PATH}",
        f"  Catalog skills: {len(catalog_names)}",
        f"  Globally active skills: {len(active_names)}",
        f"  Already installed: {len(installed_names) - len(missing)}",
        f"  Fetched via CLI: {fetched}",
        f"  Repo-local installed: {repo_local_installed}",
        f"  Symlinked to agent dirs: {symlinked}",
        f"  Stale removed: {removed_cache + removed_targets + removed_cli + removed_lock}",
    ]
    if args.dry_run:
        summary_lines.insert(0, "[DRY RUN] Skills reconciliation preview:")
    logger.info("\n".join(summary_lines))
    result = verify_reconciliation(MANIFEST_PATH)
    logger.info(
        "Reconciliation postconditions: %d catalog cached, %d globally active, %d stale lock entries, %d broken links",
        result["catalog"],
        result["active"],
        result["removed"],
        result["broken_links"],
    )
    for violation in result["violations"]:
        logger.warning("Reconciliation postcondition failed: %s", violation)


if __name__ == "__main__":
    main()
