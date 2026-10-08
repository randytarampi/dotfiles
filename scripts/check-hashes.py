#!/usr/bin/env python3
"""Verify hash trigger coverage in run_onchange scripts.

Scans .chezmoiscripts/run_onchange_*.sh.tmpl for hash trigger comments
and reports which config/script files are covered.

Exit codes:
  0 — all tracked files have hash trigger coverage
  1 — some files lack hash trigger references
"""

import json
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
    "scripts/check-mcp-parity.py",  # Makefile-only verification tool
    "scripts/cleanup-brewfiles.py",  # Makefile-only cleanup tool
    "scripts/cleanup-project.py",  # Makefile-only cleanup tool
    "scripts/show-categories.py",  # Makefile-only category management tool
    "scripts/verify-iterm2.py",  # Makefile-only verification tool
    "scripts/check-plugin-consistency.py",  # Makefile-only verification tool
    "scripts/verify-worktree-ready.py",  # Makefile-only worktree preflight helper
    "scripts/lib/worktree_readiness.py",  # Makefile-only helper; not deployed config input
    "scripts/lib/poetry_readiness.py",  # Makefile-only helper; not deployed config input
    "scripts/check-model-drift.py",  # Makefile-only verification tool
    "scripts/litellm-costs.py",  # Makefile-only verification tool
    "scripts/lib/litellm_cost.py",  # Library of the costs CLI; not deployed config input
    "scripts/litellm-oauth.py",  # Interactive one-time OAuth bootstrap; mutates ~/.config/litellm caches, not deployed config
    "scripts/litellm-db-prune.py",  # Manual maintenance CLI; targets the live gateway DB tables, not deployed config
    "scripts/check-heredocs.py",  # Makefile-only lint tool (heredoc validation)
    "scripts/anchor-review-ref.py",  # Makefile-only workflow maintenance tool
    "scripts/ci-codegraph.sh",  # CI-only asset verified by check-ci-assets
    "scripts/run-local-review.sh",  # local-only asset verified by check-ci-assets
    "scripts/onboard-agentic-review.py",  # CI-only asset verified by check-ci-assets
    "scripts/verify-ci-assets.py",  # self-verifying manifest checker
}

# Hash trigger pattern: # <path>: {{ include "<path>" | sha256sum }}
HASH_PATTERN = re.compile(
    r'^#\s+([\w/.-]+):\s*\{\{\s*include\s+"([\w/.-]+)"\s*\|\s*sha256sum\s*\}\}'
)
GATE_PATTERN = re.compile(r"\$\{(DOTFILES_RUN_[A-Z0-9_]+)")
ENV_FINGERPRINT_PATTERN = re.compile(r'env\s+"(DOTFILES_RUN_[A-Z0-9_]+)"')
PYTHON_GATE_PATTERN = re.compile(
    r"(?:os\.environ(?:\.get|\.setdefault)|os\.getenv|\b_gate)\(\s*['\"](DOTFILES_RUN_[A-Z0-9_]+)"
)
SCRIPT_REFERENCE_PATTERN = re.compile(r"scripts/[\w./-]+\.(?:sh|py)")
SOURCED_LIB_PATTERN = re.compile(
    r"(?:source|\.|\s)\s+[^\n#]*?(scripts/lib/[\w.-]+\.sh)"
)


def _child_script_gates(path: Path) -> set[str]:
    """Extract gate reads from a delegated shell or Python child script."""
    if not path.is_file():
        return set()
    content = path.read_text(encoding="utf-8")
    return set(GATE_PATTERN.findall(content)) | set(
        PYTHON_GATE_PATTERN.findall(content)
    )


def _referenced_children(template: Path) -> set[str]:
    """Resolve direct template references and CLI-contract child scripts."""
    contract_path = REPO_ROOT / "scripts/lib/cli-contract.json"
    try:
        entries = json.loads(contract_path.read_text(encoding="utf-8"))["scripts"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        entries = []
    children = {entry["path"]: entry.get("child_scripts", []) for entry in entries}
    content = template.read_text(encoding="utf-8")
    direct = set()
    for line in content.splitlines():
        if line.lstrip().startswith("#"):
            continue
        direct.update(SCRIPT_REFERENCE_PATTERN.findall(line))
    resolved = set(direct)
    pending = list(direct)
    while pending:
        current = pending.pop()
        for child in children.get(current, []):
            if child not in resolved:
                resolved.add(child)
                pending.append(child)
    return resolved


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


def find_missing_gate_fingerprints():
    """Find direct and delegated env gates lacking hash fingerprints."""
    missing = {}
    for script in sorted(CHEZMOI_SCRIPTS.glob("run_onchange_*.sh.tmpl")):
        content = script.read_text(encoding="utf-8")
        gates = set(GATE_PATTERN.findall(content))
        fingerprints = set(ENV_FINGERPRINT_PATTERN.findall(content))
        for child in _referenced_children(script):
            gates.update(_child_script_gates(REPO_ROOT / child))
        absent = sorted(gates - fingerprints)
        if absent:
            missing[script.name] = absent
    return missing


def find_trackable_files():
    """Find all config and script files that should be hash-tracked."""
    trackable = set()

    # All Python scripts in scripts/
    for f in SCRIPTS_DIR.glob("*.py"):
        rel = f"scripts/{f.name}"
        if rel in NON_TRACKED_SCRIPTS:
            continue
        trackable.add(rel)

    for f in (SCRIPTS_DIR / "lib").glob("*.py"):
        rel = f"scripts/lib/{f.name}"
        if rel not in NON_TRACKED_SCRIPTS:
            trackable.add(rel)

    # All shell scripts in scripts/
    for f in SCRIPTS_DIR.glob("*.sh"):
        rel = f"scripts/{f.name}"
        if rel not in NON_TRACKED_SCRIPTS:
            trackable.add(rel)

    # Sourced shell libraries are inputs to their templates even though they
    # are not top-level configure entrypoints.
    for template in CHEZMOI_SCRIPTS.glob("run_onchange_*.sh.tmpl"):
        content = template.read_text(encoding="utf-8")
        trackable.update(SOURCED_LIB_PATTERN.findall(content))

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
    missing_gate_fingerprints = find_missing_gate_fingerprints()

    # Files that are trackable but not covered by any hash trigger
    uncovered = trackable - covered

    print("Hash trigger coverage report")
    print("=" * 60)

    print("\nScripts with hash triggers:")
    for script, triggers in sorted(script_triggers.items()):
        print(f"  {script}: {len(triggers)} trigger(s)")

    if uncovered or missing_gate_fingerprints:
        print(
            f"\n\u26a0\ufe0f  Files lacking hash trigger coverage ({len(uncovered)}):"
        )
        for f in sorted(uncovered):
            print(f"  \u2717 {f}")
        if missing_gate_fingerprints:
            print("\nTemplates with ungated env fingerprints:")
            for script, gates in missing_gate_fingerprints.items():
                print(f"  \u2717 {script}: {', '.join(gates)}")
        print("\nThese inputs must be referenced by run_onchange_* hash triggers.")
        sys.exit(1)
    else:
        print(
            f"\n\u2713 All {len(trackable)} trackable files have hash trigger coverage."
        )
        sys.exit(0)


if __name__ == "__main__":
    main()
