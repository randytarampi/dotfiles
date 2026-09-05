#!/usr/bin/env python3
"""Verify hash trigger coverage in run_onchange scripts.

Scans .chezmoiscripts/run_onchange_*.sh.tmpl for hash trigger comments
and reports which config/script files are covered.

Exit codes:
  0 — all tracked files have hash trigger coverage
  1 — some files lack hash trigger references
"""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
CONFIGS_DIR = REPO_ROOT / "configs"
CHEZMOI_SCRIPTS = REPO_ROOT / ".chezmoiscripts"
NON_TRACKED_CONFIGS = {
    "configs/opencode/acp-agents.json",
    "configs/opencode/ci/opencode.json",
    "configs/review/code-review-prompt.md",
    "configs/review/assets-manifest.json",
}
# Scripts that are verification/audit tools, not config inputs — no hash trigger needed
NON_TRACKED_SCRIPTS = {
    "scripts/check-env-coverage.py",
    "scripts/check-hashes.py",  # self — doesn't need to track itself
    "scripts/check-docs-drift.py",  # Makefile-only verification tool
    "scripts/cleanup-brewfiles.py",  # Makefile-only cleanup tool
    "scripts/cleanup-project.py",  # Makefile-only cleanup tool
    "scripts/show-categories.py",  # Makefile-only category management tool
    "scripts/verify-iterm2.py",  # Makefile-only verification tool
    "scripts/check-plugin-consistency.py",  # Makefile-only verification tool
    "scripts/check-model-drift.py",  # Makefile-only verification tool
    "scripts/ci-codegraph.sh",  # CI-only asset verified by check-ci-assets
    "scripts/run-local-review.sh",  # local-only asset verified by check-ci-assets
    "scripts/onboard-agentic-review.py",  # CI-only asset verified by check-ci-assets
    "scripts/verify-ci-assets.py",  # self-verifying manifest checker
}

# Hash trigger pattern: # <path>: {{ include "<path>" | sha256sum }}
HASH_PATTERN = re.compile(
    r'^#\s+([\w/.-]+):\s*\{\{\s*include\s+"([\w/.-]+)"\s*\|\s*sha256sum\s*\}\}'
)


def find_hash_triggers():
    """Find all hash trigger references in run_onchange scripts."""
    covered = set()
    script_triggers = {}

    for script in sorted(CHEZMOI_SCRIPTS.glob("run_onchange_*.sh.tmpl")):
        triggers = []
        with open(script) as f:
            for line in f:
                match = HASH_PATTERN.match(line.strip())
                if match:
                    path = match.group(2)
                    covered.add(path)
                    triggers.append(path)
        if triggers:
            script_triggers[script.name] = triggers

    return covered, script_triggers


def find_trackable_files():
    """Find all config and script files that should be hash-tracked."""
    trackable = set()

    # All Python scripts in scripts/
    for f in SCRIPTS_DIR.glob("*.py"):
        rel = f"scripts/{f.name}"
        if rel in NON_TRACKED_SCRIPTS:
            continue
        trackable.add(rel)

    # All shell scripts in scripts/
    for f in SCRIPTS_DIR.glob("*.sh"):
        rel = f"scripts/{f.name}"
        if rel not in NON_TRACKED_SCRIPTS:
            trackable.add(rel)

    # All config files in configs/ (recursive)
    if CONFIGS_DIR.exists():
        for f in CONFIGS_DIR.rglob("*"):
            if f.is_file() and f.suffix in (".json", ".md", ".yaml", ".yml", ".toml"):
                rel = f.relative_to(REPO_ROOT)
                if str(rel) in NON_TRACKED_CONFIGS:
                    continue
                trackable.add(str(rel))

    return trackable


def main():
    covered, script_triggers = find_hash_triggers()
    trackable = find_trackable_files()

    # Files that are trackable but not covered by any hash trigger
    uncovered = trackable - covered

    print("Hash trigger coverage report")
    print("=" * 60)

    print("\nScripts with hash triggers:")
    for script, triggers in sorted(script_triggers.items()):
        print(f"  {script}: {len(triggers)} trigger(s)")

    if uncovered:
        print(
            f"\n\u26a0\ufe0f  Files lacking hash trigger coverage ({len(uncovered)}):"
        )
        for f in sorted(uncovered):
            print(f"  \u2717 {f}")
        print("\nThese files are inputs to configure scripts but are not referenced")
        print("by any run_onchange_* hash trigger. Add them to the relevant script.")
        sys.exit(1)
    else:
        print(
            f"\n\u2713 All {len(trackable)} trackable files have hash trigger coverage."
        )
        sys.exit(0)


if __name__ == "__main__":
    main()
