#!/usr/bin/env python3
"""Clean up Homebrew packages not declared by the dotfiles Brewfiles."""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - only used on minimal Python installs
    yaml = None

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
LIB_DIR = SCRIPT_DIR / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import logger

CATEGORY_SPECS = [
    ("dev_cli", "Brewfile", True),
    ("dev", "Brewfile.dev", True),
    ("desktop_browsers", "Brewfile.desktop.browsers", True),
    ("desktop_comms", "Brewfile.desktop.comms", True),
    ("desktop_security", "Brewfile.desktop.security", True),
    ("desktop_media", "Brewfile.desktop.media", True),
    ("desktop_utilities", "Brewfile.desktop.utilities", True),
    ("desktop_fonts", "Brewfile.desktop.fonts", True),
    ("desktop_gaming", "Brewfile.desktop.gaming", False),
    ("desktop_cloud", "Brewfile.desktop.cloud", True),
    ("desktop_productivity", "Brewfile.desktop.productivity", True),
    ("dev_ops", "Brewfile.dev.ops", True),
]
LEGACY_BREWFILE = "Brewfile.legacy"


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Clean up Homebrew packages no longer declared in any active "
            "Brewfile (preserves Brewfile.legacy packages)."
        )
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="Show what would be cleaned without making changes.",
    )
    parser.add_argument(
        "--force", action="store_true", help="Skip the confirmation prompt."
    )
    return parser.parse_args()


def load_categories():
    categories_path = REPO_ROOT / ".chezmoidata" / "categories.yaml"
    if yaml is not None:
        with categories_path.open(encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
        return data.get("categories", {})

    # Keep the standalone cleanup command usable with Homebrew's minimal Python.
    categories = {}
    in_categories = False
    for line in categories_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped == "categories:":
            in_categories = True
        elif in_categories and line.startswith("  ") and ":" in stripped:
            key, value = stripped.split(":", 1)
            categories[key] = value.split("#", 1)[0].strip().lower() == "true"
        elif in_categories and stripped and not line.startswith(" "):
            break
    return categories


def get_brewfiles():
    categories = load_categories()
    active = [
        REPO_ROOT / filename
        for key, filename, default in CATEGORY_SPECS
        if categories.get(key, default)
    ]
    legacy = REPO_ROOT / LEGACY_BREWFILE
    selected = [path for path in active + [legacy] if path.is_file()]
    return active, selected


def write_merged_brewfile(paths):
    temporary = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="dotfiles-brewfile-",
        suffix=".brewfile",
        delete=False,
    )
    try:
        for path in paths:
            temporary.write(f"# Sourced from {path.name}\n")
            temporary.write(path.read_text(encoding="utf-8"))
            temporary.write("\n")
        return Path(temporary.name)
    finally:
        temporary.close()


def run_cleanup(merged_path, dry_run):
    command = ["brew", "bundle", "cleanup", f"--file={merged_path}"]
    if not dry_run:
        command.append("--force")
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    if output:
        logger.info(output)
    # Homebrew's cleanup command has no --dry-run flag: without --force it
    # previews removals and returns 1 when removals are available.
    return 0 if dry_run and result.returncode in (0, 1) else result.returncode


def main():
    args = parse_args()
    if not shutil.which("brew"):
        logger.warning("Homebrew is not available; skipping Brewfile cleanup.")
        return 0

    active_paths, selected_paths = get_brewfiles()
    active_paths = [path for path in active_paths if path.is_file()]
    logger.info(f"Active Brewfiles: {len(active_paths)}")
    for path in active_paths:
        logger.info(f"  {path.name}")
    logger.info(
        f"Legacy preserved: {'yes' if (REPO_ROOT / LEGACY_BREWFILE).is_file() else 'no (file not found)'}"
    )

    merged_path = write_merged_brewfile(selected_paths)
    try:
        if args.diff:
            if run_cleanup(merged_path, dry_run=True) != 0:
                return 1
            cleaned = "dry-run"
        else:
            if not args.force:
                if run_cleanup(merged_path, dry_run=True) != 0:
                    return 1
                try:
                    answer = input(
                        "This will uninstall the packages listed above. Continue? [y/N] "
                    )
                except EOFError:
                    answer = ""
                if answer.strip().lower() != "y":
                    logger.info("Cleanup cancelled.")
                    return 0
            if run_cleanup(merged_path, dry_run=False) != 0:
                return 1
            cleaned = "completed"

        logger.info(
            "Brewfile cleanup complete!\n"
            f"  Active Brewfiles merged: {len(active_paths)}\n"
            "  Legacy preserved: yes\n"
            f"  Packages cleaned: {cleaned}"
        )
        return 0
    finally:
        merged_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
