#!/usr/bin/env python3
"""
OpenCode Voice Plugin Configurator.

Generates ~/.config/opencode/tui.json with tier-aware voice plugin configuration.
Supports:
  - Local Ollama LLM + whisper-cli STT (pro, local, pro-plus fallback)
  - Ollama Cloud LLM + whisper-cli STT (pro, pro-plus)
  - OpenAI LLM + OpenAI STT (plus, plus-anthropic)
  - Anthropic/Meridian LLM + OpenAI STT (anthropic, pro-plus-anthropic)

Usage:
  configure-opencode-voice.py [--preset TIER] [--dry-run] [--show-secrets] [--no-backup]

Environment variables:
  DOTFILES_USE_LOCAL_OLLAMA  - Include local Ollama endpoint (default: true)
  OPENCODE_DIR               - Override config directory
  MERIDIAN_API_KEY           - Meridian proxy API key
  MERIDIAN_HOST              - Meridian proxy host (default: 127.0.0.1)
  MERIDIAN_PORT              - Meridian proxy port (default: 3456)
  ANTHROPIC_API_KEY          - Anthropic API key (direct)
  ANTHROPIC_BASE_URL         - Anthropic base URL (indicates Meridian proxy)
  OPENAI_API_KEY             - OpenAI API key (for cloud STT)
  OLLAMA_API_KEY             - Ollama Cloud API key
  DOTFILES_WHISPER_MODEL    - Whisper model filename (default: ggml-large-v3-turbo.bin)
  DOTFILES_PIPER_VOICE       - Piper voice name (default: en_US-lessac-high)
"""

import sys
import json
import argparse
import os
import shutil
import importlib.util

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
LIB_DIR = SCRIPT_DIR if SCRIPT_DIR.endswith("lib") else os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger
from opencode_config import get_available_tiers


def load_env_file(env_path: str) -> bool:
    """Load key=value pairs from a .env file into os.environ."""
    if not os.path.exists(env_path):
        return False
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    parts = line.split("=", 1)
                    key = parts[0].strip()
                    if key.startswith("export "):
                        key = key.split(None, 1)[1].strip()
                    val = parts[1].strip()
                    if (val.startswith('"') and val.endswith('"')) or (
                        val.startswith("'") and val.endswith("'")
                    ):
                        val = val[1:-1]
                    os.environ[key] = val
        return True
    except Exception:
        return False


def get_voice_config(tier: str) -> dict:
    """Build the opencode-voice plugin config dict based on tier.

    Returns a dict suitable for the plugin entry in tui.json:
      {"endpoint": ..., "model": ..., optional "apiKeyEnv": ...,
       optional "sttEndpoint": ..., optional "sttModel": ..., optional "sttApiKeyEnv": ...}
    """
    is_anthropic_tier = tier in ("anthropic", "pro-plus-anthropic")
    is_plus_tier = tier in ("plus", "plus-anthropic")
    is_local_tier = tier.startswith("local")
    is_pro_tier = tier == "pro"

    # Determine Meridian proxy usage for Anthropic tiers
    meridian_host = os.environ.get("MERIDIAN_HOST", "127.0.0.1")
    meridian_port = os.environ.get("MERIDIAN_PORT", "3456")
    anthropic_base_url = os.environ.get("ANTHROPIC_BASE_URL", "")
    use_meridian = bool(
        os.environ.get("MERIDIAN_API_KEY", "")
        or (is_anthropic_tier and anthropic_base_url)
    )

    # ── LLM endpoint ──────────────────────────────────────────────────
    if is_local_tier:
        # Local Ollama — no API key needed
        voice_config = {
            "endpoint": "http://localhost:11434/v1",
        }
        # Pick the best local model for voice
        use_local_ollama = os.environ.get(
            "DOTFILES_USE_LOCAL_OLLAMA", "true"
        ).lower() in (
            "true",
            "1",
        )
        if use_local_ollama:
            try:
                tier_module_path = os.path.join(
                    SCRIPT_DIR, "configure-opencode-tier.py"
                )
                spec = importlib.util.spec_from_file_location(
                    "configure_opencode_tier", tier_module_path
                )
                if spec and spec.loader:
                    tier_module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(tier_module)
                    local_models = tier_module.list_local_ollama_models()
                    if local_models:
                        # Prefer audio-capable models, then largest model
                        resolved = tier_module.resolve_roles_from_list(local_models)
                        # Use the reasoning model (largest) for voice, or code-gen
                        voice_model = (
                            resolved.get("reasoning")
                            or resolved.get("code-gen")
                            or resolved.get("lightweight")
                        )
                        if voice_model:
                            # Strip ollama/ prefix if present
                            voice_config["model"] = voice_model.replace(
                                "ollama/", "", 1
                            )
            except Exception:
                pass
        if "model" not in voice_config:
            voice_config["model"] = "gemma4:e4b"

    elif is_pro_tier:
        # Ollama Cloud — gemma4:31b, key via OLLAMA_API_KEY
        voice_config = {
            "endpoint": "https://ollama.com/v1",
            "model": "gemma4:31b",
            "apiKeyEnv": "OLLAMA_API_KEY",
        }

    elif is_anthropic_tier:
        # Anthropic — via Meridian proxy or direct
        if use_meridian:
            voice_config = {
                "endpoint": f"http://{meridian_host}:{meridian_port}/v1",
                "model": "claude-sonnet-4-6",
                "apiKeyEnv": "MERIDIAN_API_KEY",
            }
        else:
            voice_config = {
                "endpoint": "https://api.anthropic.com/v1",
                "model": "claude-sonnet-4-6",
                "apiKeyEnv": "ANTHROPIC_API_KEY",
            }

    elif is_plus_tier or tier == "pro-plus":
        # OpenAI or Ollama Cloud + OpenAI STT
        voice_config = {
            "endpoint": "https://api.openai.com/v1",
            "model": "gpt-5.4-mini",
            "apiKeyEnv": "OPENAI_API_KEY",
        }

    elif tier == "pro-plus-anthropic":
        # pro-plus-anthropic: Ollama Cloud for LLM, OpenAI for STT
        voice_config = {
            "endpoint": "https://ollama.com/v1",
            "model": "gemma4:31b",
            "apiKeyEnv": "OLLAMA_API_KEY",
        }

    else:
        # Fallback: Ollama Cloud
        voice_config = {
            "endpoint": "https://ollama.com/v1",
            "model": "gemma4:31b",
            "apiKeyEnv": "OLLAMA_API_KEY",
        }

    # ── STT config ──────────────────────────────────────────────────
    # Cloud tiers use OpenAI Whisper for STT when OPENAI_API_KEY is available.
    # Local/Ollama Cloud tiers use whisper-cli (local).
    # plus/plus-anthropic use OpenAI STT (bundled with their OpenAI LLM endpoint).

    has_openai_key = bool(os.environ.get("OPENAI_API_KEY", ""))

    if is_plus_tier:
        # OpenAI tiers — use OpenAI for STT too
        voice_config["sttEndpoint"] = "https://api.openai.com/v1"
        voice_config["sttModel"] = "whisper-1"
        voice_config["sttApiKeyEnv"] = "OPENAI_API_KEY"
    elif is_anthropic_tier and has_openai_key:
        # Anthropic tiers — upgrade STT to OpenAI if key available
        voice_config["sttEndpoint"] = "https://api.openai.com/v1"
        voice_config["sttModel"] = "whisper-1"
        voice_config["sttApiKeyEnv"] = "OPENAI_API_KEY"
    elif tier == "pro-plus" and has_openai_key:
        # pro-plus — upgrade STT to OpenAI if key available
        voice_config["sttEndpoint"] = "https://api.openai.com/v1"
        voice_config["sttModel"] = "whisper-1"
        voice_config["sttApiKeyEnv"] = "OPENAI_API_KEY"
    elif tier == "pro-plus-anthropic" and has_openai_key:
        # pro-plus-anthropic — upgrade STT to OpenAI if key available
        voice_config["sttEndpoint"] = "https://api.openai.com/v1"
        voice_config["sttModel"] = "whisper-1"
        voice_config["sttApiKeyEnv"] = "OPENAI_API_KEY"
    # else: local/pro use whisper-cli (no stt config needed — plugin auto-detects)

    # ── Optional custom prompt files ─────────────────────────────────
    # These are optional — only include if the files exist
    opencode_dir = os.environ.get("OPENCODE_DIR")
    if opencode_dir:
        config_dir = os.path.abspath(os.path.expanduser(opencode_dir))
    else:
        config_dir = os.path.join(os.path.expanduser("~"), ".config", "opencode")

    stt_prompt = os.path.join(config_dir, "stt-prompt.md")
    if os.path.exists(stt_prompt):
        voice_config["sttPrompt"] = stt_prompt

    tts_auto_prompt = os.path.join(config_dir, "tts-auto-prompt.md")
    if os.path.exists(tts_auto_prompt):
        voice_config["ttsAutoPrompt"] = tts_auto_prompt

    tts_manual_prompt = os.path.join(config_dir, "tts-manual-prompt.md")
    if os.path.exists(tts_manual_prompt):
        voice_config["ttsManualPrompt"] = tts_manual_prompt

    return voice_config


def main():
    parser = argparse.ArgumentParser(
        description="Configure OpenCode voice plugin (tui.json) based on active tier."
    )
    available_tiers = get_available_tiers()
    parser.add_argument(
        "--preset",
        default="pro-plus",
        choices=available_tiers,
        help="Active OpenCode tier (determines voice LLM/STT endpoints)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print config to stdout without writing files",
    )
    parser.add_argument(
        "--show-secrets",
        action="store_true",
        help="Include API key env var names in dry-run output (keys are never printed)",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip backup of existing tui.json",
    )
    args = parser.parse_args()

    # Resolve config directory
    opencode_dir = os.environ.get("OPENCODE_DIR")
    if opencode_dir:
        config_dir_path = os.path.abspath(os.path.expanduser(opencode_dir))
    else:
        config_dir_path = os.path.join(os.path.expanduser("~"), ".config", "opencode")

    # Load .env for API keys
    env_path = os.path.join(config_dir_path, ".env")
    if not load_env_file(env_path):
        # Try home .env
        home_env = os.path.expanduser("~/.env")
        load_env_file(home_env)

    # Build voice config
    voice_config = get_voice_config(args.preset)

    # Build full tui.json
    tui_config = {
        "$schema": "https://opencode.ai/tui.json",
        "plugin": [
            ["@renjfk/opencode-voice", voice_config],
        ],
    }

    # Dry-run mode
    if args.dry_run:
        print("# Dry-run: tui.json configuration")
        print(f"# Tier: {args.preset}")
        print(f"# Config dir: {config_dir_path}")
        print()
        if args.show_secrets:
            print(json.dumps(tui_config, indent=2))
        else:
            # Redact apiKeyEnv values
            safe_config = json.loads(json.dumps(tui_config))
            for plugin_entry in safe_config.get("plugin", []):
                if isinstance(plugin_entry, list) and len(plugin_entry) == 2:
                    opts = plugin_entry[1]
                    for key in ("apiKeyEnv", "sttApiKeyEnv"):
                        if key in opts:
                            opts[key] = f"<{opts[key]}>"
            print(json.dumps(safe_config, indent=2))
        return

    # Write tui.json
    os.makedirs(config_dir_path, exist_ok=True)
    output_path = os.path.join(config_dir_path, "tui.json")

    # Backup existing config
    if os.path.exists(output_path) and not args.no_backup:
        backup_path = output_path + ".bak"
        try:
            shutil.copy2(output_path, backup_path)
            logger.info(f"Backed up existing tui.json to {backup_path}")
        except Exception as e:
            logger.warning(f"Failed to backup tui.json: {e}")

    # Merge with existing config if it has other plugins
    existing_plugins = []
    if os.path.exists(output_path):
        try:
            with open(output_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            existing_plugins = existing.get("plugin", [])
        except Exception:
            existing_plugins = []

    # Remove any existing @renjfk/opencode-voice entry
    filtered_plugins = []
    for entry in existing_plugins:
        if isinstance(entry, list) and len(entry) >= 1:
            if entry[0] == "@renjfk/opencode-voice":
                continue
        filtered_plugins.append(entry)

    # Add the new voice plugin entry at the end
    filtered_plugins.append(["@renjfk/opencode-voice", voice_config])
    tui_config["plugin"] = filtered_plugins

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(tui_config, f, indent=2)
            f.write("\n")
        logger.info(f"tui.json written to {output_path}")
    except Exception as e:
        logger.critical(f"Failed to write tui.json: {e}")
        sys.exit(1)

    # Summary
    whisper_model = os.environ.get("DOTFILES_WHISPER_MODEL", "ggml-large-v3-turbo.bin")
    piper_voice = os.environ.get("DOTFILES_PIPER_VOICE", "en_US-lessac-high")
    has_stt = "sttEndpoint" in voice_config

    summary_lines = [
        "OpenCode voice plugin configured!",
        "",
        f"Config written to: {output_path}",
        f"  • LLM endpoint: {voice_config['endpoint']}",
        f"  • LLM model: {voice_config['model']}",
    ]
    if "apiKeyEnv" in voice_config:
        summary_lines.append(f"  • LLM API key env: {voice_config['apiKeyEnv']}")
    if has_stt:
        summary_lines.extend(
            [
                f"  • STT endpoint: {voice_config['sttEndpoint']}",
                f"  • STT model: {voice_config['sttModel']}",
                f"  • STT API key env: {voice_config['sttApiKeyEnv']}",
            ]
        )
    else:
        summary_lines.extend(
            [
                "",
                "STT: using local whisper-cli (install: brew install whisper-cpp sox)",
                f"  Whisper model dir: ~/.local/share/whisper-cpp/",
                f"  Recommended model: {whisper_model}",
            ]
        )

    summary_lines.extend(
        [
            "",
            "TTS: using local Piper (install: uv tool install piper-tts)",
            f"  Piper voice dir: ~/.local/share/piper-voices/",
            f"  Recommended voice: {piper_voice}",
            "",
            "To switch tiers: configure-opencode-voice.py --preset <tier>",
            "To install models: install-opencode.sh (step 8)",
            "",
            "Voice plugin configuration complete!",
        ]
    )
    logger.info("\n".join(summary_lines))


if __name__ == "__main__":
    main()
