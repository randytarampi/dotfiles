#!/usr/bin/env python3
"""Generate ACP agent wrappers for OpenCode."""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
LIB_DIR = SCRIPT_DIR if SCRIPT_DIR.endswith("lib") else os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
DEFAULT_OUTPUT_PATH = os.path.join(REPO_ROOT, "configs", "opencode", "acp-agents.json")

import logger
from cli_helpers import add_common_args
from constants import get_ollama_local_base_url
from discover_models import list_local_ollama_models
from tier_resolve import resolve_roles_from_list

# Internal-only metadata keys that must not leak into the generated
# acp-agents.json / oh-my-opencode-slim.json — OpenCode's schema rejects
# unrecognized keys.
_INTERNAL_KEYS = frozenset({"local_fallback", "experimental"})


def _strip_internal(entry):
    """Return a copy of *entry* with internal metadata keys removed."""
    return {k: v for k, v in entry.items() if k not in _INTERNAL_KEYS}


ACP_AGENTS = {
    # Local fallbacks are intentionally limited: Copilot has no native Ollama
    # provider; OpenCode has native tier switching; Cortex and Antigravity are
    # cloud-only; Cursor and Cline are IDE-integrated.
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
        "local_fallback": True,
        "experimental": True,
    },
    "antigravity": {
        "command": "agy-acp",
        "args": [],
        "description": "Antigravity (via agy-acp bridge)",
    },
    "claude": {
        "command": "claude-agent-acp",
        "args": [],
        "description": "Claude Code via ACP adapter",
        "local_fallback": True,
    },
    "codex": {
        "command": "codex-acp",
        "args": [],
        "description": "Codex CLI via ACP adapter",
        "local_fallback": True,
    },
    "junie": {
        "command": "junie",
        "args": ["--acp", "true"],
        "description": "JetBrains Junie CLI",
        "local_fallback": True,
    },
    "cursor": {
        "command": "cursor-agent",
        "args": ["acp"],
        "description": "Cursor CLI (via cursor-agent)",
        "env": {"CURSOR_API_KEY": "${CURSOR_API_KEY}"},
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
    "pi": {
        "command": "npx",
        "args": ["-y", "pi-acp"],
        "description": "Pi coding agent (via pi-acp bridge)",
        "env": {"PI_ACP_ENABLE_EMBEDDED_CONTEXT": "true"},
        "local_fallback": True,
    },
    "cortex": {
        "command": "cortex",
        "args": ["acp", "serve", "-c", "${CORTEX_CONNECTION}"],
        "description": "Snowflake Cortex Code agent (ACP)",
        "env": {"SNOWFLAKE_HOME": "${SNOWFLAKE_HOME}"},
    },
}


def local_model():
    """Resolve the local-solo orchestrator model to a concrete Ollama ID."""
    models = list_local_ollama_models()
    resolved = resolve_roles_from_list(models) if models else {}
    model = resolved.get("solo") or resolved.get("code-gen")
    if not model:
        logger.warning(
            "No local model available for ACP agent fallback; using sentinel"
        )
        model = "ollama/no-model-available"
    return model.split("/", 1)[-1]


def active_pi_tier():
    """Return the configured Pi tier, falling back to the OpenCode tier."""
    return (
        os.environ.get("DOTFILES_PI_TIER")
        or os.environ.get("DOTFILES_OPENCODE_TIER")
        or ""
    )


def write_local_junie_config(model, dry_run):
    path = Path("~/.junie-local/model-groups.json").expanduser()
    config = {
        "providers": {
            "ollama": {
                "baseUrl": get_ollama_local_base_url().rstrip("/")
                + "/chat/completions",
                "apiType": "OpenAICompletion",
                "apiKeyEnv": "OLLAMA_API_KEY",
            }
        },
        "groups": {
            "local-solo": {
                "provider": "ollama",
                "primaryModel": model,
                "fasterModel": model,
            }
        },
    }
    if dry_run:
        logger.info(f"Would write local Junie model groups to {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def write_local_codex_config(model, dry_run):
    path = Path("~/.codex-local/config.toml").expanduser()
    content = (
        '[model_providers.ollama-local]\nname = "Ollama Local"\n'
        f'base_url = "{get_ollama_local_base_url()}"\nwire_api = "responses"\n\n'
        "[profiles.ollama-local]\n"
        f'model = "{model}"\nmodel_provider = "ollama-local"\n'
    )
    if dry_run:
        logger.info(f"Would write local Codex config to {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def build_local_agents(model):
    return {
        "gemini--local": {
            "command": "gemini",
            "args": [],
            "description": "Gemini CLI (local Ollama fallback) - EXPERIMENTAL",
            "experimental": True,
            "local_fallback": True,
            "env": {"OLLAMA_LOCAL_MODEL": model},
        },
        "claude--local": {
            "command": "claude-agent-acp",
            "args": ["--model", model],
            "description": "Claude Code (local Ollama fallback)",
            "local_fallback": True,
            "env": {
                "ANTHROPIC_BASE_URL": get_ollama_local_base_url(),
                "ANTHROPIC_AUTH_TOKEN": "ollama",
            },
        },
        "codex--local": {
            "command": "codex-acp",
            "args": ["--profile", "ollama", "--model", model],
            "description": "Codex CLI (local Ollama fallback)",
            "local_fallback": True,
            "env": {"CODEX_HOME": "${HOME}/.codex-local"},
        },
        "junie--local": {
            "command": "junie",
            "args": ["--acp", "true"],
            "description": "Junie (local Ollama fallback)",
            "local_fallback": True,
            "env": {"JUNIE_MODEL_GROUPS": "${HOME}/.junie-local/model-groups.json"},
        },
        "pi--local": {
            "command": "npx",
            "args": ["-y", "pi-acp"],
            "description": "Pi coding agent (local Ollama fallback)",
            "local_fallback": True,
            "env": {
                "PI_CODING_AGENT_DIR": "${HOME}/.pi-local/agent",
                "PI_ACP_ENABLE_EMBEDDED_CONTEXT": "true",
            },
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Generate ACP agent wrappers.")
    add_common_args(parser)
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
        "--slim-file",
        help="Optional oh-my-opencode-slim.json to merge the generated agents into",
    )
    parser.add_argument(
        "--agents",
        help="Comma-separated ACP agent names to include (default: all detected)",
    )
    args = parser.parse_args()

    try:
        detected_agents = {}
        detected_names = []
        requested_agents = {
            name.strip() for name in (args.agents or "").split(",") if name.strip()
        }
        model = local_model()
        active_tier = active_pi_tier()
        local_entries = build_local_agents(model)
        for name, entry in ACP_AGENTS.items():
            include_base = not requested_agents or name in requested_agents
            if include_base and shutil.which(entry["command"]):
                logger.info(f"Detected ACP agent: {name} ({entry['command']})")
                detected_names.append(name)
                agent_entry = _strip_internal(entry)
                agent_entry["permissionMode"] = "ask"
                agent_entry["timeoutMs"] = 300000
                detected_agents[name] = agent_entry
            elif include_base:
                logger.info(
                    f"Skipping ACP agent: {name} ({entry['command']}) not found"
                )

            local_name = f"{name}--local"
            local_entry = local_entries.get(local_name)
            if (
                entry.get("local_fallback")
                and local_entry
                and (not requested_agents or local_name in requested_agents)
            ):
                if local_name == "pi--local" and active_tier.startswith("local-"):
                    logger.info(
                        "Skipping ACP agent: pi--local (active tier is local-*; "
                        "@pi already provides local models)"
                    )
                    continue
                if shutil.which(local_entry["command"]):
                    logger.info(
                        f"Detected ACP agent: {local_name} ({local_entry['command']})"
                    )
                    local_entry = _strip_internal(local_entry)
                    local_entry["permissionMode"] = "ask"
                    local_entry["timeoutMs"] = 900000
                    detected_agents[local_name] = local_entry
                    detected_names.append(local_name)
                else:
                    logger.info(
                        f"Skipping ACP agent: {local_name} ({local_entry['command']}) not found"
                    )

        if any(name in detected_agents for name in ("junie--local", "codex--local")):
            write_local_junie_config(model, args.dry_run)
            write_local_codex_config(model, args.dry_run)

        output_path = os.path.abspath(os.path.expanduser(args.output))
        output_dir = os.path.dirname(output_path) or "."
        if not args.dry_run:
            os.makedirs(output_dir, exist_ok=True)

        if args.dry_run:
            logger.info(f"Would write ACP agents to {output_path}")
        else:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump({"acpAgents": detected_agents}, f, indent=2)
                f.write("\n")

        if args.slim_file:
            slim_path = os.path.abspath(os.path.expanduser(args.slim_file))
            with open(slim_path, "r", encoding="utf-8") as f:
                slim_data = json.load(f)
            slim_data["acpAgents"] = detected_agents
            if args.dry_run:
                logger.info(f"Would merge acpAgents into {slim_path}")
            else:
                with open(slim_path, "w", encoding="utf-8") as f:
                    json.dump(slim_data, f, indent=2)
                    f.write("\n")
                logger.info(f"acpAgents merged into {slim_path}")

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
