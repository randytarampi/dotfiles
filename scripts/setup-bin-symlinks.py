#!/usr/bin/env python3
"""Create or update _dot--* command symlinks in ~/.dotfiles/bin/."""

import argparse
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))

import cli_helpers  # noqa: E402
import logger  # noqa: E402


def _source_scripts(source_dir):
    return Path(source_dir).expanduser().resolve() if source_dir else SCRIPT_DIR


def setup_bin_symlinks(source_dir=None, *, dry_run=False):
    source_scripts = _source_scripts(source_dir)
    dotfiles = Path.home() / ".dotfiles"
    dotfiles_bin = dotfiles / "bin"
    dotfiles_scripts = dotfiles / "scripts"

    logger.info("Setting up bin symlinks...")

    if dry_run:
        logger.info("[DRY RUN] Would ensure %s exists", dotfiles)
    else:
        dotfiles.mkdir(parents=True, exist_ok=True)

    if not dotfiles_scripts.is_symlink() and not dotfiles_scripts.is_dir():
        if dry_run:
            logger.info(
                "[DRY RUN] Would create scripts symlink: %s → %s",
                dotfiles_scripts,
                source_scripts,
            )
        else:
            dotfiles_scripts.symlink_to(source_scripts)
            logger.info(
                "Created scripts symlink: %s → %s", dotfiles_scripts, source_scripts
            )

    if dry_run:
        logger.info("[DRY RUN] Would ensure %s exists", dotfiles_bin)
    else:
        dotfiles_bin.mkdir(parents=True, exist_ok=True)

    created = skipped = removed = 0

    # Match the shell's ordering: Python scripts first, then shell scripts.
    scripts = sorted(
        list(source_scripts.glob("*.py")) + list(source_scripts.glob("*.sh"))
    )
    for script in scripts:
        if not script.is_file():
            continue
        base = script.name.removesuffix(".py").removesuffix(".sh")
        target_link = dotfiles_bin / f"_dot--{base}"
        if not target_link.is_symlink():
            if dry_run:
                logger.info(
                    "[DRY RUN] Would create symlink: %s → %s", target_link, script
                )
            else:
                target_link.symlink_to(script)
                logger.info("Created symlink: %s → %s", target_link, script)
            created += 1
        else:
            current_target = os.readlink(target_link)
            if current_target != str(script):
                if dry_run:
                    logger.info(
                        "[DRY RUN] Would update symlink: %s → %s", target_link, script
                    )
                else:
                    target_link.unlink()
                    target_link.symlink_to(script)
                    logger.info("Updated symlink: %s → %s", target_link, script)
                created += 1
            else:
                skipped += 1

    for link in dotfiles_bin.glob("_dot--*"):
        if not link.is_symlink():
            continue
        target = os.readlink(link)
        if not Path(target).is_file():
            if dry_run:
                logger.info(
                    "[DRY RUN] Would remove stale symlink: %s → %s", link, target
                )
            else:
                link.unlink()
                logger.info("Removed stale symlink: %s → %s", link, target)
            removed += 1

    logger.info(
        "Bin symlinks: %d created, %d existing, %d removed.",
        created,
        skipped,
        removed,
    )
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    cli_helpers.add_common_args(parser)
    parser.add_argument("source_dir", nargs="?", help="Directory containing scripts")
    args = parser.parse_args(argv)
    return setup_bin_symlinks(args.source_dir, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
