#!/usr/bin/env python3
"""Verify that generated config files exist for enabled features.

Read-only drift check. For each DOTFILES_RUN_*_SETUP gate that is enabled,
verifies that the expected generated output files exist.

Exit codes:
  0 — all enabled features have their output files
  1 — one or more enabled features are missing output files
"""

import os
import sys
import json
from pathlib import Path

HOME = Path.home()

# Gate → list of (description, file path) checks
CHECKS = [
    (
        "DOTFILES_RUN_OPENCODE_SETUP",
        "OpenCode config",
        [
            HOME / ".config/opencode/opencode.json",
            HOME / ".config/opencode/oh-my-opencode-slim.json",
        ],
    ),
    (
        "DOTFILES_RUN_MCP_SETUP",
        "MCP configs",
        [
            HOME / ".config/opencode/mcp",
        ],
    ),
    (
        "DOTFILES_RUN_MOZART_SETUP",
        "Mozart router config",
        [
            HOME / ".mozart/mozart.json",
        ],
    ),
    (
        "DOTFILES_RUN_SMALLCODE_SETUP",
        "SmallCode config",
        [
            HOME / ".config/smallcode/config.toml",
            HOME / ".config/smallcode/.env",
        ],
    ),
    (
        "DOTFILES_RUN_AGENT_GUIDANCE_SETUP",
        "Agent guidance files",
        [
            HOME / "AGENTS.md",
            HOME / ".claude/CLAUDE.md",
            HOME / ".codex/AGENTS.md",
            HOME / ".cursor/AGENTS.md",
            HOME / ".config/opencode/AGENTS.md",
            HOME / ".gemini/GEMINI.md",
        ],
    ),
]


def main():
    exit_code = 0

    for gate, description, paths in CHECKS:
        enabled = os.environ.get(gate, "0") == "1"
        if not enabled:
            print(f"  \u2298 {description} (gate {gate}=0, skipped)")
            continue

        all_exist = True
        for path in paths:
            if path.exists():
                print(f"  \u2713 {description}: {path}")
            else:
                print(f"  \u2717 {description}: MISSING {path}")
                all_exist = False

        if not all_exist:
            exit_code = 1

    # Also check CodeGraph MCP registration in opencode.json
    codegraph_gate = os.environ.get("DOTFILES_RUN_CODEGRAPH_SETUP", "0") == "1"
    if codegraph_gate:
        opencode_json = HOME / ".config/opencode/opencode.json"
        if opencode_json.exists():
            try:
                with open(opencode_json) as f:
                    config = json.load(f)
                mcps = config.get("mcp", {})
                if "codegraph" in mcps:
                    print(f"  \u2713 CodeGraph MCP: registered in opencode.json")
                else:
                    print(f"  \u2717 CodeGraph MCP: not found in opencode.json")
                    exit_code = 1
            except (json.JSONDecodeError, KeyError):
                print(f"  \u2717 CodeGraph MCP: could not parse opencode.json")
                exit_code = 1
        else:
            print(f"  \u2717 CodeGraph MCP: opencode.json not found")
            exit_code = 1
    else:
        print(f"  \u2298 CodeGraph MCP (gate DOTFILES_RUN_CODEGRAPH_SETUP=0, skipped)")

    if exit_code == 0:
        print("\nAll enabled features have their output files.")
    else:
        print(
            "\nSome enabled features are missing output files. Run 'make configure' to regenerate."
        )

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
