#!/usr/bin/env python3
"""
Configure optional model providers for the Codex CLI.

Adds Meridian, Ollama Cloud, and Ollama Local as available providers in
~/.codex/config.toml without changing Codex's default provider (OpenAI).
Preserves Codex's runtime-managed configuration (marketplaces, plugins,
desktop settings, MCP servers). Switch providers at runtime with:
  codex -c model_provider=meridian -m <model>
"""

import argparse
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = SCRIPT_DIR if SCRIPT_DIR.endswith("lib") else os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger
from cli_helpers import add_common_args
from file_utils import backup_file, write_text_file

PROVIDER_CONFIG = """[model_providers.meridian]
name = "Meridian"
base_url = "http://127.0.0.1:3456/v1"
wire_api = "responses"
requires_openai_auth = true
env_key = "MERIDIAN_API_KEY"

[model_providers.ollama-cloud]
name = "Ollama Cloud"
base_url = "https://ollama.com/v1"
wire_api = "chat"
env_key = "OLLAMA_API_KEY"

[model_providers.ollama-local]
name = "Ollama Local"
base_url = "http://localhost:11434/v1"
wire_api = "chat"
"""


def main():
    parser = argparse.ArgumentParser(
        description="Configure model providers in the Codex CLI configuration."
    )
    add_common_args(parser)
    args = parser.parse_args()

    config_dir = os.path.expanduser("~/.codex")
    config_path = os.path.join(config_dir, "config.toml")

    try:
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as config_file:
                content = config_file.read()
        else:
            content = ""
        original_content = content

        content = re.sub(r"^model_provider\s*=.*$\n?", "", content, flags=re.MULTILINE)
        for provider in ("meridian", "ollama-cloud", "ollama-local"):
            content = re.sub(
                r"\n*\[model_providers\.%s\].*?(?=\n\[|\Z)" % re.escape(provider),
                "",
                content,
                flags=re.DOTALL,
            )
        new_content = content.rstrip()
        if new_content:
            new_content += "\n\n"
        new_content += PROVIDER_CONFIG

        if new_content == original_content:
            logger.info(f"Providers already configured in {config_path}")
        elif args.dry_run:
            logger.info(f"Would configure providers in {config_path}")
        else:
            os.makedirs(config_dir, exist_ok=True)
            if os.path.exists(config_path) and not args.no_backup:
                backup_path = backup_file(config_path, enabled=True)
                if backup_path:
                    logger.info(f"Backed up existing config.toml to {backup_path}")
            write_text_file(config_path, new_content, backup=False)
            logger.info(f"Codex configuration written to {config_path}")
    except OSError as exc:
        logger.critical(f"Failed to update Codex configuration: {exc}")
        sys.exit(1)

    summary_lines = [
        "Codex providers configured!",
        "",
        f"Configuration: {config_path}",
        "  • Default: OpenAI (unchanged)",
        "  • meridian: http://127.0.0.1:3456/v1 — responses",
        "  • ollama-cloud: https://ollama.com/v1 — chat",
        "  • ollama-local: http://localhost:11434/v1 — chat",
        "",
        "Switch providers at runtime:",
        "  codex -c model_provider=meridian -m claude-sonnet-5",
        "  codex -c model_provider=ollama-cloud -m glm-5.2",
        "  codex -c model_provider=ollama-local -m qwen2.5-coder",
        "",
        "Configure script complete!",
    ]
    logger.info("\n".join(summary_lines))


if __name__ == "__main__":
    main()
