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
ALL_ENV_VAR_PATTERN = re.compile(r"\b[A-Z][A-Z0-9_]+\b")
DOCUMENTED_VAR_PATTERN = re.compile(r"^#?\s*(DOTFILES_[A-Z_]+)=")
DOCUMENTED_ENV_VAR_PATTERN = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*)=")
IGNORED_VARS = {
    "DOTFILES_BIN",
    "DOTFILES_DIR",
    "DOTFILES_RUN_",
    "DOTFILES_SCRIPTS",
}

DEPRECATED_VARS = {
    "DOTFILES_RUN_AGENT_GUIDANCE_SETUP_OLD": "DOTFILES_RUN_AGENT_GUIDANCE_SETUP",
    "DOTFILES_RUN_CODEGRAPH_SETUP_OLD": "DOTFILES_RUN_CODEGRAPH_SETUP",
    "DOTFILES_RUN_INSTALL_PACKAGES": "DOTFILES_RUN_PACKAGES_SETUP",
    "DOTFILES_RUN_MACOS_DEFAULTS": "DOTFILES_RUN_MACOS_DEFAULTS_SETUP",
    "DOTFILES_RUN_MACOS_SECURITY": "DOTFILES_RUN_MACOS_SECURITY_SETUP",
    "DOTFILES_RUN_MERIDIAN_LAUNCHD": "DOTFILES_RUN_MERIDIAN_SETUP",
    "DOTFILES_RUN_MOZART_SETUP_OLD": "DOTFILES_RUN_MOZART_SETUP",
    "DOTFILES_RUN_OPENCODE_PLUGINS_SETUP": "DOTFILES_RUN_OPENCODE_TOOLS_SETUP",
    "DOTFILES_RUN_OPENCODE_WEB": "DOTFILES_RUN_OPENCODE_WEB_SETUP",
    "DOTFILES_RUN_SECRETS_SETUP_OLD": "DOTFILES_RUN_SECRETS_SETUP",
}

KNOWN_ALIASES = {
    "GH_TOKEN": ["GITHUB_TOKEN"],
    "OLLAMA_HOST": ["OLLAMA_LOCAL_HOST", "OLLAMA_LOCAL_PORT"],
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
                if file_path.name == Path(__file__).name:
                    continue
                if file_path.suffix == ".py" and (
                    file_path.name.startswith("check-")
                    or file_path.name.startswith("verify-")
                ):
                    continue
                content = file_path.read_text()
                referenced.update(ENV_VAR_PATTERN.findall(content))

    return {
        var
        for var in referenced
        if var not in IGNORED_VARS and var != "DOTFILES_PROJECT_"
    }


def find_referenced_env_names():
    """Find all-uppercase environment names for alias shadowing checks."""
    referenced = set()
    for scan_dir, suffixes in SCAN_DIRS:
        if not scan_dir.exists():
            continue
        for file_path in scan_dir.rglob("*"):
            if (
                "__pycache__" in file_path.parts
                or file_path.name == Path(__file__).name
            ):
                continue
            if file_path.is_file() and file_path.suffix in suffixes:
                if file_path.suffix == ".py" and (
                    file_path.name.startswith("check-")
                    or file_path.name.startswith("verify-")
                ):
                    continue
                referenced.update(ALL_ENV_VAR_PATTERN.findall(file_path.read_text()))
    return referenced


def find_documented_vars():
    """Find DOTFILES_* variables documented in .env.example."""
    documented = set()

    for line in ENV_EXAMPLE.read_text().splitlines():
        match = DOCUMENTED_VAR_PATTERN.match(line)
        if match:
            documented.add(match.group(1))

    return documented


def find_documented_env_vars():
    """Find all environment variables documented in .env.example."""
    documented = set()

    for line in ENV_EXAMPLE.read_text().splitlines():
        match = DOCUMENTED_ENV_VAR_PATTERN.match(line)
        if match:
            documented.add(match.group(1))

    return documented


def find_deprecated_vars(referenced):
    """Return deprecated variables referenced by repository files."""
    return referenced.intersection(DEPRECATED_VARS)


def alias_is_explained(lines, canonical, alias):
    """Whether a nearby comment documents the canonical/alias relationship."""
    for index, line in enumerate(lines):
        if canonical not in line or alias not in line:
            continue
        if line.lstrip().startswith("#") or "#" in line:
            return True
        nearby = " ".join(lines[max(0, index - 1) : index + 2])
        if "#" in nearby:
            return True
    return False


def ownership_info(referenced):
    """Classify referenced DOTFILES variables by their ownership prefix."""
    info = []
    for var in referenced:
        if var.startswith("DOTFILES_RUN_") or var.startswith("DOTFILES_PROJECT_"):
            info.append((var, "repo"))
    return info


def main():
    referenced = find_referenced_vars()
    all_referenced = find_referenced_env_names()
    documented = find_documented_vars()
    documented_env_vars = find_documented_env_vars()
    missing = referenced - documented - set(DEPRECATED_VARS)
    deprecated = find_deprecated_vars(referenced)
    example_lines = ENV_EXAMPLE.read_text().splitlines()

    print("DOTFILES_* env var documentation coverage report")
    print("=" * 60)

    if deprecated:
        print("\nDeprecated vars still referenced:")
        for var in sorted(deprecated):
            print(f"  ⚠ {var} → {DEPRECATED_VARS[var]}")

    print("\nKnown alias pairs:")
    for canonical, aliases in KNOWN_ALIASES.items():
        for alias in aliases:
            if canonical in all_referenced and alias in all_referenced:
                print(f"  ⚠ {canonical} / {alias}: shadowing — both are referenced")
            elif alias in all_referenced:
                print(f"  ⚠ {alias}: should migrate to canonical {canonical}")
            else:
                print(f"  ✓ {canonical} / {alias}: no shadowing")

    ownership = ownership_info(referenced)
    if ownership:
        print("\nDOTFILES_* ownership (informational):")
        for var, owner in sorted(ownership):
            print(f"  • {var}: {owner}")

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
