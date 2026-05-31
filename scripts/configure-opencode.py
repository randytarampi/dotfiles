#!/usr/bin/env python3
"""
Configure OpenCode Helper.
Constructs the opencode.json configuration based on presets, mode, local Ollama, and templates.
"""

import sys
import json
import argparse
import os
import shutil
import subprocess
import importlib.util

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
LIB_DIR = SCRIPT_DIR if SCRIPT_DIR.endswith("lib") else os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger


def load_env_file(env_path: str) -> bool:
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


def main():
    parser = argparse.ArgumentParser(
        description="Configure OpenCode json generator and orchestration."
    )
    parser.add_argument(
        "--preset",
        default="pro-plus",
        choices=[
            "pro",
            "pro-plus",
            "pro-plus-anthropic",
            "plus",
            "plus-anthropic",
            "anthropic",
            "local",
        ],
    )
    parser.add_argument("--mode", default="global", choices=["global", "project"])
    args = parser.parse_args()

    configs_dir_path = os.path.abspath(
        os.path.join(os.path.dirname(SCRIPT_DIR), "configs")
    )

    opencode_dir = os.environ.get("OPENCODE_DIR")
    if opencode_dir:
        config_dir_path = os.path.abspath(os.path.expanduser(opencode_dir))
    else:
        config_dir_path = os.path.join(os.path.expanduser("~"), ".config", "opencode")

    os.makedirs(config_dir_path, exist_ok=True)

    # Load .env
    env_path = os.path.join(config_dir_path, ".env")
    if load_env_file(env_path):
        logger.info(f"Sourced {env_path}")
    else:
        logger.warning(
            f"{env_path} not found — run configure-ai.py first for MCP server configs"
        )

    use_local_env = os.environ.get("DOTFILES_USE_LOCAL_OLLAMA", "true").lower()
    with_local_ollama = use_local_env in ["true", "1"]

    local_ollama_models = []
    if with_local_ollama:
        try:
            tier_module_path = os.path.join(SCRIPT_DIR, "configure-opencode-tier.py")
            spec = importlib.util.spec_from_file_location(
                "configure_opencode_tier", tier_module_path
            )
            if spec and spec.loader:
                tier_module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(tier_module)
                local_ollama_models = tier_module.list_local_ollama_models()
        except Exception:
            pass

    # Generate MCP Config
    mcp_args = [
        sys.executable,
        os.path.join(SCRIPT_DIR, "configure-mcp-tool.py"),
        "--mode",
        args.mode,
        "--env-file",
        env_path,
        "--dry-run",
        "--show-secrets",
        "--no-backup",
        "opencode",
    ]
    mcp_json_str = "{}"
    try:
        result = subprocess.run(mcp_args, capture_output=True, text=True, timeout=15)
        # Extract JSON from dry-run output (skip comment header lines starting with #)
        json_lines = [
            line
            for line in result.stdout.strip().splitlines()
            if not line.strip().startswith("#")
        ]
        mcp_json_str = "\n".join(json_lines).strip()
        if not mcp_json_str:
            mcp_json_str = "{}"
    except Exception as e:
        logger.warning(f"Failed to generate MCP config: {e}")

    try:
        mrg_data = json.loads(mcp_json_str) if mcp_json_str else {}
        mcp_config = mrg_data.get("mcp", {})
    except Exception:
        mcp_config = {}

    # Read externalized models configs
    openai_models_path = os.path.join(
        configs_dir_path, "opencode", "openai-models.json"
    )
    ollama_cloud_models_path = os.path.join(
        configs_dir_path, "opencode", "ollama-cloud-models.json"
    )
    anthropic_models_path = os.path.join(
        configs_dir_path, "opencode", "anthropic-models.json"
    )

    openai_models = {}
    ollama_cloud_models = {}
    anthropic_models = {}

    if os.path.exists(openai_models_path):
        with open(openai_models_path, "r", encoding="utf-8") as f:
            openai_models = json.load(f).get("models", {})
    if os.path.exists(ollama_cloud_models_path):
        with open(ollama_cloud_models_path, "r", encoding="utf-8") as f:
            ollama_cloud_models = json.load(f).get("models", {})
    if os.path.exists(anthropic_models_path):
        with open(anthropic_models_path, "r", encoding="utf-8") as f:
            anthropic_models = json.load(f).get("models", {})

    # Decode local ollama
    local_ollama = {}
    if local_ollama_models:
        models_obj = {name: {"name": name} for name in sorted(local_ollama_models)}
        local_ollama = {
            "models": models_obj,
            "name": "Ollama",
            "npm": "@ai-sdk/openai-compatible",
            "options": {"baseURL": "http://localhost:11434/v1"},
        }

    meridian_plugin_path = os.environ.get("MERIDIAN_PLUGIN_PATH", "")
    include_anthropic = args.preset in [
        "pro-plus-anthropic",
        "plus-anthropic",
        "anthropic",
    ] or bool(meridian_plugin_path)

    if args.mode == "project":
        config = {
            "$schema": "https://opencode.ai/config.json",
            "mcp": mcp_config,
            "provider": {},
            "plugin": [],
            "agent": {},
        }

        if (
            include_anthropic
            and anthropic_models
            and args.preset in ["pro-plus-anthropic", "plus-anthropic", "anthropic"]
        ):
            config["provider"]["anthropic"] = {"models": anthropic_models}

        if meridian_plugin_path:
            config["plugin"].append(meridian_plugin_path)

        config["agent"]["explore"] = {"disable": True}
        config["agent"]["general"] = {"disable": True}

        if not config["plugin"]:
            del config["plugin"]
        if not config["mcp"]:
            del config["mcp"]
        if not config["provider"]:
            del config["provider"]

    else:
        config = {
            "$schema": "https://opencode.ai/config.json",
            "mcp": mcp_config,
            "lsp": True,
            "provider": {},
            "plugin": [
                "oh-my-opencode-slim",
                "@tarquinen/opencode-dcp@latest",
                [
                    "@plannotator/opencode@latest",
                    {
                        "workflow": "plan-agent",
                        "planningAgents": ["orchestrator", "plan", "council"],
                    },
                ],
                "opencode-plugin-openspec",
                "opencode-vibeguard",
            ],
            "agent": {"explore": {"disable": True}, "general": {"disable": True}},
            "disabled_providers": [
                "google-vertex-anthropic",
                "google-vertex",
                "amazon-bedrock",
            ],
        }

        if args.preset == "local":
            if local_ollama:
                config["provider"]["ollama"] = local_ollama
            config["disabled_providers"].extend(["openai", "anthropic", "ollama-cloud"])
        elif args.preset == "anthropic":
            if include_anthropic and anthropic_models:
                config["provider"]["anthropic"] = {"models": anthropic_models}
            config["disabled_providers"].extend(["openai", "ollama-cloud", "ollama"])
        elif args.preset == "plus-anthropic":
            if openai_models:
                config["provider"]["openai"] = {"models": openai_models}
            if include_anthropic and anthropic_models:
                config["provider"]["anthropic"] = {"models": anthropic_models}
            config["disabled_providers"].extend(["ollama-cloud", "ollama"])
        else:
            if openai_models:
                config["provider"]["openai"] = {"models": openai_models}
            if ollama_cloud_models and args.preset != "plus":
                config["provider"]["ollama-cloud"] = {"models": ollama_cloud_models}
            if local_ollama and args.preset not in ["plus", "pro"]:
                config["provider"]["ollama"] = local_ollama
            if include_anthropic and anthropic_models:
                config["provider"]["anthropic"] = {"models": anthropic_models}
            if args.preset == "pro":
                config["provider"].pop("openai", None)
                config["provider"].pop("anthropic", None)
                config["disabled_providers"].extend(["openai", "anthropic", "ollama"])
            elif args.preset == "plus":
                config["provider"].pop("anthropic", None)
                config["disabled_providers"].extend(
                    ["anthropic", "ollama-cloud", "ollama"]
                )

        if meridian_plugin_path:
            config["plugin"].append(meridian_plugin_path)

    # Write opencode.json
    output_path = os.path.join(config_dir_path, "opencode.json")
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
            f.write("\n")
        logger.info("opencode.json written")
    except Exception as e:
        logger.critical(f"Failed to write opencode.json: {e}")
        sys.exit(1)

    if args.mode == "project":
        summary_lines = [
            "OpenCode project config written!",
            "",
            f"Config written to: {output_path}",
            "  • Project MCPs, providers, and agent overrides",
            "",
            "Note: oh-my-opencode-slim.json is NOT written in project mode.",
            "      Use configure-opencode-tier.py in the project directory to set project-tier preset.",
            "Configure script complete!",
        ]
        logger.info("\n".join(summary_lines))
        return

    # global mode:
    # 2. Write vibeguard.config.json
    vibeguard_src = os.path.join(configs_dir_path, "opencode", "vibeguard.config.json")
    if os.path.exists(vibeguard_src):
        vibeguard_dst = os.path.join(config_dir_path, "vibeguard.config.json")
        try:
            shutil.copy(vibeguard_src, vibeguard_dst)
            logger.info("vibeguard.config.json written (sensitive-string redaction)")
        except Exception as e:
            logger.warning(f"Failed to copy vibeguard.config.json: {e}")
    else:
        logger.warning(f"vibeguard.config.json not found at {vibeguard_src}")

    # 3. Write oh-my-opencode-slim.json
    presets_json_path = os.path.join(
        configs_dir_path, "opencode", "oh-my-opencode-slim.json"
    )
    logger.info(
        f"Writing {os.path.join(config_dir_path, 'oh-my-opencode-slim.json')}..."
    )
    if not os.path.exists(presets_json_path):
        logger.critical(f"presets.json not found at {presets_json_path}")
        sys.exit(1)

    try:
        shutil.copy(
            presets_json_path, os.path.join(config_dir_path, "oh-my-opencode-slim.json")
        )
        logger.info("oh-my-opencode-slim.json written (base config)")
    except Exception as e:
        logger.critical(f"Failed to copy oh-my-opencode-slim.json: {e}")
        sys.exit(1)

    # 4. Set active tier
    logger.info(f"Setting active tier to {args.preset}...")
    try:
        tier_args = [
            sys.executable,
            os.path.join(SCRIPT_DIR, "configure-opencode-tier.py"),
        ]
        if with_local_ollama:
            tier_args.append("--with-local-fallbacks")
        tier_args.append(args.preset)
        subprocess.run(tier_args, check=True)
        logger.info(f"Active tier set to {args.preset}")
    except Exception as e:
        logger.critical(f"Failed to set active tier: {e}")
        sys.exit(1)

    summary_lines = [
        "OpenCode configured!",
        "",
        f"Config written to: {config_dir_path}",
        "  • opencode.json (providers, MCP servers, plugins)",
        f"  • oh-my-opencode-slim.json (all presets, active: {args.preset})",
        "  • vibeguard.config.json (sensitive-string redaction)",
        "",
        "To switch tiers:",
        "     configure-opencode-tier.py pro",
        "     configure-opencode-tier.py pro-plus",
        "     configure-opencode-tier.py pro-plus-anthropic",
        "     configure-opencode-tier.py plus",
        "     configure-opencode-tier.py anthropic",
        "     configure-opencode-tier.py local",
        "",
        "To regenerate with local ollama:",
        "     set DOTFILES_USE_LOCAL_OLLAMA=1 and re-run configure-opencode.py",
        "",
        "To add/update Meridian proxy plugin:",
        "     configure-meridian.py",
        "",
        "Configure script complete!",
    ]
    logger.info("\n".join(summary_lines))


if __name__ == "__main__":
    main()
