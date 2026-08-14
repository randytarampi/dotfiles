#!/usr/bin/env python3
"""
verify-brewfile-completeness.py — Verify that all Brewfile categories referenced in categories.yaml exist.
"""

import sys
import os
import re

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger


def category_to_brewfile(category):
    """Map a category name to its Brewfile filename.

    Convention: dev_cli -> Brewfile, everything else -> Brewfile.<category>
    with underscores replaced by dots (e.g. desktop_gaming -> Brewfile.desktop.gaming).
    """
    if category == "dev_cli":
        return "Brewfile"
    return f"Brewfile.{category.replace('_', '.')}"


def main():
    dotfiles_dir = os.path.dirname(SCRIPT_DIR)
    categories_file = os.path.join(dotfiles_dir, ".chezmoidata", "categories.yaml")

    if not os.path.isfile(categories_file):
        logger.critical(f"ERROR: categories.yaml not found at {categories_file}")
        sys.exit(1)

    logger.info("Checking Brewfile categories against categories.yaml...")
    print()

    categories = []
    # Simple YAML key extraction via regex (matches '  key:')
    key_pattern = re.compile(r"^\s+([a-z_]+):")
    try:
        with open(categories_file, "r", encoding="utf-8") as f:
            for line in f:
                match = key_pattern.match(line)
                if match:
                    categories.append(match.group(1))
    except Exception as e:
        logger.critical(f"Failed to read categories file: {e}")
        sys.exit(1)

    errors = 0

    for cat in categories:
        brewfile = category_to_brewfile(cat)

        filepath = os.path.join(dotfiles_dir, brewfile)
        if os.path.isfile(filepath):
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    lines = sum(1 for _ in f)
                logger.info(f"  ✓ {brewfile} ({lines} lines)")
            except Exception:
                logger.info(f"  ✓ {brewfile}")
        else:
            logger.error(f"  ✗ MISSING: {brewfile} (category: {cat})")
            errors += 1

    print()
    if errors == 0:
        logger.info("All Brewfile categories have corresponding files.")
        sys.exit(0)
    else:
        logger.error(f"{errors} missing Brewfile(s) detected.")
        sys.exit(1)


if __name__ == "__main__":
    main()
