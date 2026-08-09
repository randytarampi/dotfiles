#!/usr/bin/env python3
"""Generate ACP agent wrappers for OpenCode."""

import argparse
import json
import os
import shutil
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
LIB_DIR = SCRIPT_DIR if SCRIPT_DIR.endswith("lib") else os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
DEFAULT_OUTPUT_PATH = os.path.join(REPO_ROOT, "configs", "opencode", "acp-agents.json")

import logger

ACP_AGENTS = {
    "opencode": {
        "command": "opencode",
        "args": ["acp"],
        "description": "OpenCode (recursive delegation — use for sub-agent fanout)",
        "prompt": "You are an OpenCode ACP wrapper. Delegate coding/research tasks to the underlying OpenCode instance. Avoid re-delegating to opencode to prevent recursion.",
        "orchestratorPrompt": "Use the opencode ACP agent for sub-agent fanout when you need parallel OpenCode sessions. Do NOT chain opencode→opencode recursively.",
    },
    "gemini": {
        "command": "gemini",
        "args": ["--acp"],
        "description": "Gemini CLI — Google's coding agent",
    },
    "antigravity": {
        "command": "agy-acp",
        "args": [],
        "description": "Antigravity (via agy-acp bridge)",
    },
    "claude-code": {
        "command": "claude-agent-acp",
        "args": [],
        "description": "Claude Code via ACP adapter",
    },
    "codex": {
        "command": "codex-acp",
        "args": [],
        "description": "Codex CLI via ACP adapter",
    },
    "junie": {
        "command": "junie",
        "args": ["--acp", "true"],
        "description": "JetBrains Junie CLI",
    },
    "cursor": {
        "command": "agent",
        "args": ["acp"],
        "description": "Cursor CLI",
    },
    "cline": {
        "command": "cline",
        "args": ["--acp"],
        "description": "Cline CLI",
    },
    "copilot": {
        "command": "copilot",
        "args": ["--acp", "--stdio"],
        "description": "GitHub Copilot CLI (public preview)",
    },
}


def main():
    parser = argparse.ArgumentParser(description="Generate ACP agent wrappers.")
    parser.add_argument(
        "--preset",
        help="Active OpenCode tier name (accepted for caller compatibility; "
        "wrapperModel is resolved by OMO-Slim from its active preset)",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT_PATH,
        help="Output path for acp-agents.json",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Accepted for consistency; no-op for generated output",
    )
    args = parser.parse_args()

    try:
        detected_agents = {}
        detected_names = []
        for name, entry in ACP_AGENTS.items():
            if shutil.which(entry["command"]):
                logger.info(f"Detected ACP agent: {name} ({entry['command']})")
                detected_names.append(name)
                agent_entry = dict(entry)
                agent_entry["permissionMode"] = "ask"
                agent_entry["timeoutMs"] = 300000
                detected_agents[name] = agent_entry
            else:
                logger.info(
                    f"Skipping ACP agent: {name} ({entry['command']}) not found"
                )

        output_path = os.path.abspath(os.path.expanduser(args.output))
        output_dir = os.path.dirname(output_path) or "."
        os.makedirs(output_dir, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump({"acpAgents": detected_agents}, f, indent=2)
            f.write("\n")

        summary_lines = [
            "ACP agents configured!",
            "",
            f"Output written to: {output_path}",
            f"Detected agents: {len(detected_names)}",
            f"  • {', '.join(detected_names) if detected_names else 'none'}",
        ]
        logger.info("\n".join(summary_lines))
    except Exception as e:
        logger.critical(f"Failed to configure ACP agents: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
