#!/usr/bin/env python3
"""Create and reconcile the command symlinks in ``~/.dotfiles/bin``."""

import argparse
import glob
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
LIB_DIR = SCRIPT_DIR / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import logger
from cli_helpers import add_common_args


def _symlink(link: Path, target: Path, dry_run: bool, action: str) -> None:
    if dry_run:
        logger.info("[DRY RUN] Would %s symlink: %s → %s", action, link, target)
        return
    if action == "Updated":
        link.unlink()
    link.symlink_to(target)
    logger.info("%s symlink: %s → %s", action, link, target)


def setup_symlinks(
    source_scripts: Path, home: Path, dry_run: bool = False
) -> tuple[int, int, int]:
    """Apply the shell implementation's symlink rules and return its counts."""
    dotfiles = home / ".dotfiles"
    dotfiles_scripts = dotfiles / "scripts"
    dotfiles_bin = dotfiles / "bin"

    if dry_run:
        logger.info("[DRY RUN] Would create directory: %s", dotfiles)
    else:
        dotfiles.mkdir(parents=True, exist_ok=True)

    if not dotfiles_scripts.is_symlink() and not dotfiles_scripts.is_dir():
        _symlink(dotfiles_scripts, source_scripts, dry_run, "Created")

    if dry_run:
        logger.info("[DRY RUN] Would create directory: %s", dotfiles_bin)
    else:
        dotfiles_bin.mkdir(parents=True, exist_ok=True)

    created = skipped = removed = 0
    # Keep the shell glob order and duplicate-basename behaviour: a .sh wrapper
    # processed after its .py implementation wins the final target.
    scripts = sorted(glob.glob(str(source_scripts / "*.py"))) + sorted(
        glob.glob(str(source_scripts / "*.sh"))
    )
    for script_name in scripts:
        script = Path(script_name)
        if not script.is_file():
            continue
        base = script.name.removesuffix(".py").removesuffix(".sh")
        target_link = dotfiles_bin / f"_dot--{base}"
        if not target_link.is_symlink():
            _symlink(target_link, script, dry_run, "Created")
            created += 1
        elif os.readlink(target_link) != str(script):
            _symlink(target_link, script, dry_run, "Updated")
            created += 1
        else:
            skipped += 1

    for link_name in glob.glob(str(dotfiles_bin / "_dot--*")):
        link = Path(link_name)
        if not link.is_symlink():
            continue
        target = Path(os.readlink(link))
        if not target.is_file():
            if dry_run:
                logger.info(
                    "[DRY RUN] Would remove stale symlink: %s → %s", link, target
                )
            else:
                link.unlink()
                logger.info("Removed stale symlink: %s → %s", link, target)
            removed += 1

    logger.info(
        "Bin symlinks: %s created, %s existing, %s removed.",
        created,
        skipped,
        removed,
    )
    return created, skipped, removed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create or update ~/.dotfiles/bin command symlinks."
    )
    add_common_args(parser)
    parser.add_argument(
        "source_dir",
        nargs="?",
        type=Path,
        default=SCRIPT_DIR,
        help="Source scripts directory (default: this script's directory)",
    )
    args = parser.parse_args()
    source_scripts = args.source_dir.expanduser().resolve()
    logger.info("Setting up bin symlinks...")
    setup_symlinks(source_scripts, Path.home(), args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
