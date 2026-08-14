#!/usr/bin/env python3
"""Show per-machine category state and manage overrides in chezmoi.toml.

Reads committed defaults from .chezmoidata/categories.yaml and per-machine
overrides from ~/.config/chezmoi/chezmoi.toml [data.categories], then prints
the effective state. With --toggle, interactively enables/disables categories.

Usage:
    show-categories.py              # Show current state (read-only)
    show-categories.py --toggle     # Interactively toggle categories
    show-categories.py --enable desktop_gaming,desktop_dev
    show-categories.py --disable desktop_gaming
"""

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
LIB_DIR = SCRIPT_DIR / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import logger  # noqa: E402

REPO_ROOT = SCRIPT_DIR.parent
CATEGORIES_YAML = REPO_ROOT / ".chezmoidata" / "categories.yaml"
CHEZMOI_TOML = Path.home() / ".config" / "chezmoi" / "chezmoi.toml"


def load_yaml_categories():
    """Load committed defaults from categories.yaml."""
    try:
        import yaml
    except ModuleNotFoundError:
        yaml = None

    if yaml is not None:
        with CATEGORIES_YAML.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("categories", {})

    # Minimal parser
    categories = {}
    in_categories = False
    for line in CATEGORIES_YAML.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped == "categories:":
            in_categories = True
        elif in_categories and line.startswith("  ") and ":" in stripped:
            key, value = stripped.split(":", 1)
            categories[key.strip()] = value.split("#", 1)[0].strip().lower() == "true"
        elif in_categories and stripped and not line.startswith(" "):
            break
    return categories


def load_overrides():
    """Load per-machine overrides from chezmoi.toml [data.categories]."""
    overrides = {}
    if not CHEZMOI_TOML.is_file():
        return overrides
    in_data_categories = False
    for line in CHEZMOI_TOML.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_data_categories = stripped == "[data.categories]"
            continue
        if in_data_categories and "=" in stripped and not stripped.startswith("#"):
            key, value = stripped.split("=", 1)
            key = key.strip().strip('"')
            value = value.strip()
            if value.lower() in ("true", "false"):
                overrides[key] = value.lower() == "true"
    return overrides


def has_data_categories_section():
    """Check if chezmoi.toml already has a [data.categories] section."""
    if not CHEZMOI_TOML.is_file():
        return False
    for line in CHEZMOI_TOML.read_text(encoding="utf-8").splitlines():
        if line.strip() == "[data.categories]":
            return True
    return False


def update_overrides(new_overrides):
    """Write/update the [data.categories] section in chezmoi.toml."""
    content = CHEZMOI_TOML.read_text(encoding="utf-8") if CHEZMOI_TOML.is_file() else ""

    lines = content.splitlines()
    output = []
    in_section = False
    skip_section = False
    has_section = False

    for line in lines:
        stripped = line.strip()
        if stripped == "[data.categories]":
            in_section = True
            has_section = True
            skip_section = True  # skip old entries, we'll rewrite
            continue
        if in_section and stripped.startswith("[") and stripped.endswith("]"):
            in_section = False
            skip_section = False
            output.append(line)  # keep the next section header
            continue
        if skip_section:
            continue
        output.append(line)

    # Append the new section
    if output and output[-1].strip():
        output.append("")
    output.append("[data.categories]")
    for key in sorted(new_overrides):
        output.append(f"{key} = {'true' if new_overrides[key] else 'false'}")

    CHEZMOI_TOML.parent.mkdir(parents=True, exist_ok=True)
    CHEZMOI_TOML.write_text("\n".join(output) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(
        description="Show per-machine Brewfile/wingetfile category state and manage overrides."
    )
    parser.add_argument(
        "--toggle", action="store_true", help="Interactively toggle categories"
    )
    parser.add_argument(
        "--enable", metavar="CATS", help="Comma-separated categories to enable"
    )
    parser.add_argument(
        "--disable", metavar="CATS", help="Comma-separated categories to disable"
    )
    args = parser.parse_args()

    defaults = load_yaml_categories()
    overrides = load_overrides()

    # Build effective state
    effective = {}
    for key in sorted(defaults):
        effective[key] = overrides.get(key, defaults[key])

    # Handle --enable / --disable
    if args.enable or args.disable:
        new_overrides = dict(overrides)
        if args.enable:
            for cat in args.enable.split(","):
                cat = cat.strip()
                if cat in defaults:
                    new_overrides[cat] = True
                else:
                    logger.warning(f"Unknown category: {cat}")
        if args.disable:
            for cat in args.disable.split(","):
                cat = cat.strip()
                if cat in defaults:
                    new_overrides[cat] = False
                else:
                    logger.warning(f"Unknown category: {cat}")
        update_overrides(new_overrides)
        logger.info(f"Updated {CHEZMOI_TOML}")
        # Re-read effective state
        effective = {}
        overrides = new_overrides
        for key in sorted(defaults):
            effective[key] = overrides.get(key, defaults[key])

    # Handle --toggle (interactive)
    if args.toggle:
        new_overrides = dict(overrides)
        for key in sorted(defaults):
            current = effective[key]
            source = "override" if key in overrides else "default"
            try:
                answer = input(
                    f"  {key} [{current}] (source: {source}) — toggle? [y/N] "
                )
            except EOFError:
                answer = ""
            if answer.strip().lower() == "y":
                new_overrides[key] = not current
        if new_overrides != overrides:
            update_overrides(new_overrides)
            logger.info(f"Updated {CHEZMOI_TOML}")
            effective = {}
            overrides = new_overrides
            for key in sorted(defaults):
                effective[key] = overrides.get(key, defaults[key])

    # Print state
    logger.info("Brewfile/wingetfile category state:")
    logger.info(f"  Defaults: {CATEGORIES_YAML}")
    logger.info(f"  Overrides: {CHEZMOI_TOML}")
    print()
    for key in sorted(effective):
        value = effective[key]
        source = "override" if key in overrides else "default"
        status = "ON " if value else "off"
        print(f"  {status}  {key:<25} ({source})")

    active = sum(1 for v in effective.values() if v)
    print()
    logger.info(f"{active}/{len(effective)} categories active on this machine.")


if __name__ == "__main__":
    main()
