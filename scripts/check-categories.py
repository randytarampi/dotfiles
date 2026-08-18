#!/usr/bin/env python3
"""Verify Brewfile/wingetfile category registries are in sync.

Four registries must agree on the category set:
  1. .chezmoidata/categories.yaml — gate definitions
  2. scripts/sync-brewfiles.py — CATEGORY_SPECS list
  3. .chezmoiscripts/run_onchange_04-install-packages.sh.tmpl — hash triggers
  4. Brewfile/wingetfile files on disk

This script diffs all four sources and reports drift.
"""

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

CATEGORY_YAML = REPO_ROOT / ".chezmoidata" / "categories.yaml"
SYNC_SCRIPT = REPO_ROOT / "scripts" / "sync-brewfiles.py"
INSTALL_TEMPLATE = (
    REPO_ROOT / ".chezmoiscripts" / "run_onchange_04-install-packages.sh.tmpl"
)


def get_categories_from_yaml() -> set[str]:
    """Extract category names from categories.yaml."""
    content = CATEGORY_YAML.read_text()
    cats: set[str] = set()
    for line in content.splitlines():
        m = re.match(r"^\s+(\w+):\s+(true|false)\s*#", line)
        if m:
            cats.add(m.group(1))
    return cats


def get_categories_from_sync_script() -> set[str]:
    """Extract category names from sync-brewfiles.py CATEGORY_SPECS."""
    content = SYNC_SCRIPT.read_text()
    cats: set[str] = set()
    # Match CategorySpec("category_name", ...)
    for m in re.finditer(r'CategorySpec\(\s*"(\w+)"', content):
        cats.add(m.group(1))
    return cats


def get_categories_from_template() -> set[str]:
    """Extract category names from hash trigger comments in the install template."""
    content = INSTALL_TEMPLATE.read_text()
    cats: set[str] = set()
    # Hash trigger lines like: # desktop.browsers: {{ include "Brewfile.desktop.browsers" | sha256sum }}
    for m in re.finditer(
        r"^#\s+([\w.]+):\s+\{\{\s*include\s+\"Brewfile", content, re.MULTILINE
    ):
        cat_name = m.group(1).replace(".", "_")
        # The template uses "base" as the label for the core Brewfile,
        # but the actual category name is "dev_cli".
        if cat_name == "base":
            cat_name = "dev_cli"
        cats.add(cat_name)
    return cats


def get_categories_from_files() -> set[str]:
    """Extract category names from Brewfile/wingetfile filenames on disk."""
    cats: set[str] = set()
    for f in REPO_ROOT.glob("Brewfile*"):
        if f.name == "Brewfile":
            cats.add("dev_cli")
        elif f.name.startswith("Brewfile."):
            suffix = f.name[len("Brewfile.") :]
            cats.add(suffix.replace(".", "_"))
    for f in REPO_ROOT.glob("wingetfile*"):
        if f.name == "wingetfile":
            cats.add("dev_cli")
        elif f.name.startswith("wingetfile."):
            suffix = f.name[len("wingetfile.") :]
            cat = suffix.replace(".", "_")
            # wingetfile categories should match Brewfile categories
            cats.add(cat)
    return cats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify Brewfile/wingetfile category registries are in sync.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="No-op (this check is read-only; --dry-run has no effect).",
    )
    args = parser.parse_args()
    _ = args

    sources = {
        "categories.yaml": get_categories_from_yaml(),
        "sync-brewfiles.py": get_categories_from_sync_script(),
        "install template": get_categories_from_template(),
        "files on disk": get_categories_from_files(),
    }

    all_cats = set()
    for cats in sources.values():
        all_cats.update(cats)

    has_drift = False
    for name, cats in sources.items():
        missing = all_cats - cats
        extra = cats - all_cats
        if missing:
            has_drift = True
            print(f"  {name}: MISSING {sorted(missing)}")
        if extra:
            has_drift = True
            print(f"  {name}: EXTRA {sorted(extra)}")

    if has_drift:
        print("\nCategory registry drift detected. All four sources must agree.")
        print("Sources: categories.yaml, sync-brewfiles.py CATEGORY_SPECS,")
        print("         install template hash triggers, Brewfile/wingetfile files")
        return 1

    print(f"All {len(all_cats)} categories in sync across all 4 registries.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
