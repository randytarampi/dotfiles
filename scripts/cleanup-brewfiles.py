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

LEGACY_BREWFILE = "Brewfile.legacy"


def category_to_brewfile(category):
    """Map a category name to its Brewfile filename.

    Convention: dev_cli -> Brewfile, everything else -> Brewfile.<category>
    with underscores replaced by dots (e.g. desktop_gaming -> Brewfile.desktop.gaming).
    """
    if category == "dev_cli":
        return "Brewfile"
    return f"Brewfile.{category.replace('_', '.')}"


def load_category_specs():
    """Read category names and defaults from categories.yaml, returning
    (category, brewfile, default) tuples — replaces the hardcoded CATEGORY_SPECS."""
    categories_path = REPO_ROOT / ".chezmoidata" / "categories.yaml"
    specs = []
    if yaml is not None:
        with categories_path.open(encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
        for key, value in (data.get("categories") or {}).items():
            if key == "legacy":
                continue  # legacy is handled separately
            specs.append((key, category_to_brewfile(key), bool(value)))
    else:
        # Minimal parser for Homebrew's stripped Python
        in_categories = False
        for line in categories_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped == "categories:":
                in_categories = True
            elif in_categories and line.startswith("  ") and ":" in stripped:
                key, value = stripped.split(":", 1)
                key = key.strip()
                if key == "legacy":
                    continue
                specs.append(
                    (
                        key,
                        category_to_brewfile(key),
                        value.split("#", 1)[0].strip().lower() == "true",
                    )
                )
            elif in_categories and stripped and not line.startswith(" "):
                break
    return specs


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
    """Load category settings, merging .chezmoidata/categories.yaml with per-machine
    overrides from ~/.config/chezmoi/chezmoi.toml (which takes precedence)."""
    categories_path = REPO_ROOT / ".chezmoidata" / "categories.yaml"
    categories = {}

    if yaml is not None:
        with categories_path.open(encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
        categories = data.get("categories", {})
    else:
        # Keep the standalone cleanup command usable with Homebrew's minimal Python.
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

    # Merge per-machine overrides from chezmoi.toml [data.categories] (takes precedence)
    chezmoi_config = Path.home() / ".config" / "chezmoi" / "chezmoi.toml"
    if chezmoi_config.is_file():
        override_categories = _parse_toml_categories(chezmoi_config)
        categories.update(override_categories)

    return categories


def _parse_toml_categories(toml_path):
    """Extract [data.categories] overrides from a chezmoi.toml file.

    Uses a lightweight parser to avoid a toml dependency — we only need
    boolean values under the [data.categories] table.
    """
    categories = {}
    try:
        content = toml_path.read_text(encoding="utf-8")
    except OSError:
        return categories

    in_data_categories = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_data_categories = stripped == "[data.categories]"
            continue
        if in_data_categories and "=" in stripped and not stripped.startswith("#"):
            key, value = stripped.split("=", 1)
            key = key.strip().strip('"')
            value = value.strip()
            if value.lower() in ("true", "false"):
                categories[key] = value.lower() == "true"

    return categories


def get_brewfiles():
    categories = load_categories()
    category_specs = load_category_specs()
    active = [
        REPO_ROOT / filename
        for key, filename, default in category_specs
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
