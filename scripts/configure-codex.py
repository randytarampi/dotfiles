#!/usr/bin/env python3
"""
Configure optional model providers for the Codex CLI.

Adds Meridian, Ollama Cloud, Ollama Local, and optionally GitHub Copilot as available providers in
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

PROFILES_START = "# BEGIN DOTFILES MANAGED PROFILES"
PROFILES_END = "# END DOTFILES MANAGED PROFILES"

BASE_PROVIDER_CONFIG = """[model_providers.meridian]
name = "Meridian"
base_url = "http://127.0.0.1:3456/v1"
wire_api = "responses"
requires_openai_auth = true
env_key = "MERIDIAN_API_KEY"

[model_providers.ollama-cloud]
name = "Ollama Cloud"
base_url = "https://ollama.com/v1"
wire_api = "responses"
env_key = "OLLAMA_API_KEY"
"""

PROFILES_CONFIG = """# BEGIN DOTFILES MANAGED PROFILES
[profiles.meridian]
model = "claude-sonnet-5"
model_provider = "meridian"
model_reasoning_effort = "high"

[profiles.ollama-cloud]
model = "ollama-cloud/glm-5.2"
model_provider = "ollama-cloud"

[profiles.ollama]
model = "local"
model_provider = "ollama"

[profiles.local-solo]
model = "local-solo"
model_provider = "ollama"

[profiles.copilot]
model = "copilot-model-id"
model_provider = "github-copilot"
# END DOTFILES MANAGED PROFILES
"""

COPILOT_PROVIDER_CONFIG = """[model_providers.github-copilot]
name = "GitHub Copilot"
base_url = "https://api.githubcopilot.com/v1"
env_key = "GITHUB_TOKEN"
wire_api = "responses"
"""


def build_provider_config():
    config = BASE_PROVIDER_CONFIG
    if os.environ.get("GITHUB_TOKEN", "").strip():
        config += "\n" + COPILOT_PROVIDER_CONFIG
    return config


def strip_managed_profiles(content):
    # Strip properly marked blocks (BEGIN...END)
    content = re.sub(
        r"\n*" + re.escape(PROFILES_START) + r".*?" + re.escape(PROFILES_END) + r"\n*",
        "\n",
        content,
        flags=re.DOTALL,
    )
    # Strip orphaned profile sections from older runs that wrote profiles
    # without the BEGIN marker, preventing duplicate-key accumulation.
    # Transitional dual-strip: remove old profile names for one release cycle.
    for profile in (
        "meridian",
        "ollama-cloud",
        "ollama-local",
        "ollama",
        "local-solo",
        "copilot",
    ):
        content = re.sub(
            r"\n*\[profiles\.%s\].*?(?=\n\[|\Z)" % re.escape(profile),
            "",
            content,
            flags=re.DOTALL,
        )
    # Strip any remaining orphaned END markers
    content = re.sub(
        r"\n*# END DOTFILES MANAGED PROFILES\n*",
        "\n",
        content,
    )
    return content


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
        # Strip old custom provider IDs (transitional: ollama-local was renamed, ollama is now built-in).
        for provider in (
            "meridian",
            "ollama-cloud",
            "ollama-local",
            "ollama",
            "github-copilot",
        ):
            content = re.sub(
                r"\n*\[model_providers\.%s\].*?(?=\n\[|\Z)" % re.escape(provider),
                "",
                content,
                flags=re.DOTALL,
            )
        content = strip_managed_profiles(content)
        new_content = content.rstrip()
        if new_content:
            new_content += "\n\n"
        new_content += build_provider_config().rstrip()
        new_content += "\n\n" + PROFILES_CONFIG.rstrip()

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
        "  • ollama-cloud: https://ollama.com/v1 — responses",
        "  • ollama: built-in (http://localhost:11434/v1 — responses)",
        "  • github-copilot: https://api.githubcopilot.com/v1 — responses (when GITHUB_TOKEN is set)",
        "  • profiles: meridian, ollama-cloud, ollama, local-solo, copilot",
        "",
        "Switch providers at runtime:",
        "  codex -c model_provider=meridian -m claude-sonnet-5",
        "  codex -c model_provider=ollama-cloud -m glm-5.2",
        "  codex -c model_provider=ollama -m qwen2.5-coder",
        "",
        "Configure script complete!",
    ]
    logger.info("\n".join(summary_lines))


if __name__ == "__main__":
    main()
