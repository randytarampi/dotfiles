#!/usr/bin/env python3
"""Verify DOTFILES_* env var documentation coverage. Scans scripts/, .chezmoiscripts/, and docs/ for DOTFILES_* references and reports any not documented in dot_dotfiles/shell/.env.example. Exit codes: 0 — all referenced DOTFILES_* vars are documented; 1 — some vars are missing from .env.example."""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = REPO_ROOT / "dot_dotfiles" / "shell" / ".env.example"
SCAN_DIRS = [
    (REPO_ROOT / "scripts", {".py", ".sh"}),
    (REPO_ROOT / ".chezmoiscripts", {".sh", ".tmpl"}),
    (REPO_ROOT / "docs", {".md"}),
]

ENV_VAR_PATTERN = re.compile(r"\bDOTFILES_[A-Z_]+\b")
DOCUMENTED_VAR_PATTERN = re.compile(r"^#?\s*(DOTFILES_[A-Z_]+)=")
IGNORED_VARS = {
    "DOTFILES_BIN",
    "DOTFILES_DIR",
    "DOTFILES_RUN_",
    "DOTFILES_RUN_AGENT_GUIDANCE_SETUP_OLD",
    "DOTFILES_RUN_CODEGRAPH_SETUP_OLD",
    "DOTFILES_RUN_INSTALL_PACKAGES",
    "DOTFILES_RUN_MACOS_DEFAULTS",
    "DOTFILES_RUN_MACOS_SECURITY",
    "DOTFILES_RUN_MERIDIAN_LAUNCHD",
    "DOTFILES_RUN_MOZART_SETUP_OLD",
    "DOTFILES_RUN_OPENCODE_PLUGINS_SETUP",
    "DOTFILES_RUN_OPENCODE_WEB",
    "DOTFILES_RUN_SECRETS_SETUP_OLD",
    "DOTFILES_SCRIPTS",
}


def find_referenced_vars():
    """Find DOTFILES_* variables referenced by scripts and documentation."""
    referenced = set()

    for scan_dir, suffixes in SCAN_DIRS:
        if not scan_dir.exists():
            continue
        for file_path in scan_dir.rglob("*"):
            if "__pycache__" in file_path.parts:
                continue
            if file_path.is_file() and file_path.suffix in suffixes:
                referenced.update(ENV_VAR_PATTERN.findall(file_path.read_text()))

    return referenced - IGNORED_VARS


def find_documented_vars():
    """Find DOTFILES_* variables documented in .env.example."""
    documented = set()

    for line in ENV_EXAMPLE.read_text().splitlines():
        match = DOCUMENTED_VAR_PATTERN.match(line)
        if match:
            documented.add(match.group(1))

    return documented


def main():
    referenced = find_referenced_vars()
    documented = find_documented_vars()
    missing = referenced - documented

    print("DOTFILES_* env var documentation coverage report")
    print("=" * 60)

    if missing:
        print(f"\n⚠ {len(missing)} vars missing from .env.example:")
        for var in sorted(missing):
            print(f"  ✗ {var}")
        print("\nAdd each missing variable to dot_dotfiles/shell/.env.example.")
        sys.exit(1)

    print(
        f"\n✓ All {len(referenced)} referenced DOTFILES_* vars are documented in .env.example."
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
