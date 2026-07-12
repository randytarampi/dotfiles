#!/usr/bin/env python3
"""
Configure OpenCode Helper.
Constructs the opencode.json configuration based on presets, mode, local Ollama, and templates.
Also generates tui.json for the voice plugin via configure-opencode-voice.py.
"""

import sys
import json
import argparse
import os
import shutil
import subprocess
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
LIB_DIR = SCRIPT_DIR if SCRIPT_DIR.endswith("lib") else os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger
from constants import (
    get_ollama_local_base_url,
    get_provider_base_url,
    check_ollama_daemon,
)
from opencode_config import (
    get_available_tiers,
    build_tier_args,
    get_preset_providers,
    get_slim_config_path,
)
from env import load_env
from caddy_domains import load_domains
from tier_resolve import list_local_ollama_models
from models_dev import (
    fetch_models_dev,
    get_ollama_context_length,
    build_model_entry,
)

DEFAULT_CADDY_ZONES_CONFIG = "~/.config/caddy/ddns-zones.json"


def expand_path(path_value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(path_value)))


def build_opencode_server_config() -> dict[str, object] | None:
    if os.environ.get("DOTFILES_RUN_OPENCODE_WEB_SETUP", "0") != "1":
        return None

    access_mode = (
        os.environ.get("CADDY_ACCESS", "localhost").strip().lower() or "localhost"
    )
    port = int(os.environ.get("OPENCODE_SERVER_PORT", "4096") or "4096")
    zones_path = expand_path(DEFAULT_CADDY_ZONES_CONFIG)
    domains = load_domains(zones_path)

    cors_origins = ["https://localhost"]
    cors_origins.extend(f"https://{domain}" for domain in domains)

    seen_origins: set[str] = set()
    cors: list[str] = []
    for origin in cors_origins:
        if origin in seen_origins:
            continue
        seen_origins.add(origin)
        cors.append(origin)

    mdns_enabled = access_mode in ("lan", "public")
    mdns_domain = "opencode.local"
    if domains:
        mdns_domain = domains[0]

    return {
        "port": port,
        "hostname": "127.0.0.1",
        "mdns": mdns_enabled,
        "mdnsDomain": mdns_domain,
        "cors": cors,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Configure OpenCode json generator and orchestration."
    )
    available_tiers = get_available_tiers()
    parser.add_argument(
        "--preset",
        default="pro-plus",
        choices=available_tiers,
    )
    parser.add_argument("--mode", default="global", choices=["global", "project"])
    parser.add_argument(
        "--local-fallback-preset",
        default=None,
        help="Which local tier's placeholder pattern to use for local fallbacks (default: local)",
    )
    parser.add_argument(
        "--local-fallback-placeholder",
        action="append",
        default=[],
        help="Override _local:<category> resolution (e.g. vision=ollama/gemma4:e4b)",
    )
    parser.add_argument(
        "--local-fallback-role",
        action="append",
        default=[],
        help="Override local model for a role (e.g. observer=ollama/qwen3.5:9b-mlx)",
    )
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
    env_loaded = load_env(env_path)
    if env_loaded:
        logger.info(f"Sourced {env_path}")
    else:
        fallback_path = os.path.join(os.path.expanduser("~"), ".env")
        if load_env(fallback_path):
            logger.info(f"Sourced {fallback_path} (fallback)")
        else:
            logger.warning(
                f"{env_path} not found — run configure-secrets.py first for MCP server configs"
            )

    use_local_env = os.environ.get("DOTFILES_USE_LOCAL_OLLAMA", "true").lower()
    with_local_ollama = use_local_env in ["true", "1"]

    local_ollama_models = []
    if with_local_ollama:
        try:
            local_ollama_models = list_local_ollama_models()
        except Exception:
            pass

    # Check if local Ollama daemon can proxy cloud models
    _, can_proxy_cloud = check_ollama_daemon()

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

    # Fetch model metadata from models.dev (24h-cached, graceful degradation)
    models_dev_data = fetch_models_dev()

    # Decode local ollama
    local_ollama = {}
    if local_ollama_models:
        model_names = sorted(
            m["name"] if isinstance(m, dict) else str(m) for m in local_ollama_models
        )
        # Enrich local Ollama models with context_length from `ollama show`
        # (ground truth — models.dev has no bare `ollama` provider). Local
        # models are free, so no cost field.
        models_obj = {}
        for name in model_names:
            ctx = get_ollama_context_length(name)
            models_obj[name] = build_model_entry(
                name, models_dev_data, "ollama-cloud", ollama_context=ctx
            )
        local_ollama = {
            "models": models_obj,
            "name": "Ollama",
            "npm": "@ai-sdk/openai-compatible",
            "options": {"baseURL": get_ollama_local_base_url()},
        }

    meridian_plugin_path = os.environ.get("MERIDIAN_PLUGIN_PATH", "")
    # include_anthropic governs the anthropic provider block in global mode.
    # Project mode derives providers from the preset instead (see below).
    include_anthropic = args.preset in [
        "pro-plus-anthropic",
        "plus-anthropic",
        "anthropic",
    ] or bool(meridian_plugin_path)

    # Allow access to cross-platform temp directories (/tmp, macOS
    # $TMPDIR, Windows %TEMP%) without prompting. These are used by
    # subagent fanout, build tools, and the orchestrator's own
    # configure-opencode-project.py (tempfile.mkdtemp).
    _external_dir_permissions = {
        "/tmp/**": "allow",
        "/var/tmp/**": "allow",
        "/var/folders/**": "allow",
        "/private/tmp/**": "allow",
        "/private/var/folders/**": "allow",
        "~/AppData/Local/Temp/**": "allow",
    }

    if args.mode == "project":
        # Project configs must be self-sufficient: emit every provider the
        # selected preset references, and reset disabled_providers so an
        # unrelated global tier cannot suppress them. This makes project
        # presets orthogonal to the global tier (e.g. global `pro` disables
        # anthropic, but a project `anthropic` preset still works).
        try:
            needed_providers = get_preset_providers(args.preset, get_slim_config_path())
        except Exception as e:
            logger.warning(
                f"Could not derive required providers for preset "
                f"'{args.preset}' ({e}); project config may be incomplete."
            )
            needed_providers = set()

        config = {
            "$schema": "https://opencode.ai/config.json",
            "mcp": mcp_config,
            "provider": {},
            "plugin": [],
            "agent": {},
            # Allow access to cross-platform temp directories without
            # prompting. Project configs must be self-sufficient (same
            # rationale as disabled_providers reset below).
            "permission": {
                "external_directory": _external_dir_permissions,
            },
            # Always reset; project presets must not inherit a global tier's
            # disabled_providers (e.g. global `pro` disables anthropic).
            "disabled_providers": [],
        }

        if "openai" in needed_providers and openai_models:
            config["provider"]["openai"] = {
                "models": {
                    mid: build_model_entry(mid, models_dev_data, "openai")
                    for mid in openai_models
                }
            }
        if "anthropic" in needed_providers and anthropic_models:
            config["provider"]["anthropic"] = {
                "models": {
                    mid: build_model_entry(mid, models_dev_data, "anthropic")
                    for mid in anthropic_models
                }
            }
        # Project mode uses the direct ollama-cloud provider rather than the
        # merged local+cloud `ollama` block, so configs remain portable across
        # machines whether or not the local daemon proxies cloud.
        if "ollama-cloud" in needed_providers and ollama_cloud_models:
            config["provider"]["ollama-cloud"] = {
                "models": {
                    mid: build_model_entry(mid, models_dev_data, "ollama-cloud")
                    for mid in ollama_cloud_models
                }
            }
        if "ollama" in needed_providers and local_ollama:
            config["provider"]["ollama"] = local_ollama

        if meridian_plugin_path:
            config["plugin"].append(meridian_plugin_path)

        # Mirror oh-my-opencode-slim PR #520: disable the full OpenCode
        # built-in agent set replaced by OMOS (build, explore, general, plan).
        # Preserve any existing agent entries instead of wholesale-replacing.
        for _agent_name in ("build", "explore", "general", "plan"):
            _existing = config["agent"].get(_agent_name)
            config["agent"][_agent_name] = {
                **(_existing if isinstance(_existing, dict) else {}),
                "disable": True,
            }

        if not config["plugin"]:
            del config["plugin"]
        if not config["mcp"]:
            del config["mcp"]
        if not config["provider"]:
            del config["provider"]
        # Note: disabled_providers is intentionally retained even when empty,
        # so it overrides (clears) any inherited global disabled_providers.

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
                "@ramtinj95/opencode-tokenscope@latest",
            ],
            "agent": {
                "build": {"disable": True},
                "explore": {"disable": True},
                "general": {"disable": True},
                "plan": {"disable": True},
            },
            "permission": {
                "external_directory": _external_dir_permissions,
            },
            "disabled_providers": [
                "google-vertex-anthropic",
                "google-vertex",
                "amazon-bedrock",
            ],
        }

        if args.preset.startswith("local"):
            if local_ollama:
                config["provider"]["ollama"] = local_ollama
            config["disabled_providers"].extend(["openai", "anthropic", "ollama-cloud"])
        elif args.preset == "anthropic":
            if include_anthropic and anthropic_models:
                enriched_anthropic = {
                    mid: build_model_entry(mid, models_dev_data, "anthropic")
                    for mid in anthropic_models
                }
                config["provider"]["anthropic"] = {"models": enriched_anthropic}
            config["disabled_providers"].extend(["openai", "ollama-cloud", "ollama"])
        elif args.preset == "plus-anthropic":
            if openai_models:
                enriched_openai = {
                    mid: build_model_entry(mid, models_dev_data, "openai")
                    for mid in openai_models
                }
                config["provider"]["openai"] = {"models": enriched_openai}
            if include_anthropic and anthropic_models:
                enriched_anthropic = {
                    mid: build_model_entry(mid, models_dev_data, "anthropic")
                    for mid in anthropic_models
                }
                config["provider"]["anthropic"] = {"models": enriched_anthropic}
            config["disabled_providers"].extend(["ollama-cloud", "ollama"])
        else:
            if openai_models:
                enriched_openai = {
                    mid: build_model_entry(mid, models_dev_data, "openai")
                    for mid in openai_models
                }
                config["provider"]["openai"] = {"models": enriched_openai}
            if can_proxy_cloud and ollama_cloud_models and args.preset != "plus":
                # Unified: merge local + cloud Ollama under single provider
                combined_models = {}
                if local_ollama and args.preset not in ["plus", "pro"]:
                    combined_models.update(local_ollama.get("models", {}))
                # Add cloud models with :cloud suffix, enriched with
                # models.dev metadata (looked up as ollama-cloud/<model>)
                # and ground-truth context_length from `ollama show`.
                for model_name in ollama_cloud_models:
                    cloud_name = (
                        f"{model_name}:cloud"
                        if not model_name.endswith(":cloud")
                        else model_name
                    )
                    ctx = get_ollama_context_length(cloud_name)
                    combined_models[cloud_name] = build_model_entry(
                        cloud_name,
                        models_dev_data,
                        "ollama-cloud",
                        ollama_context=ctx,
                    )
                if combined_models:
                    config["provider"]["ollama"] = {
                        "models": combined_models,
                        "name": "Ollama",
                        "npm": "@ai-sdk/openai-compatible",
                        "options": {"baseURL": get_ollama_local_base_url()},
                    }
                    config["disabled_providers"].append("ollama-cloud")
                elif local_ollama and args.preset not in ["plus", "pro"]:
                    # Cloud-capable but no cloud models pulled; keep local-only provider
                    config["provider"]["ollama"] = local_ollama
            elif ollama_cloud_models and args.preset != "plus":
                # Not cloud-capable: use direct ollama-cloud provider
                enriched_cloud = {
                    mid: build_model_entry(mid, models_dev_data, "ollama-cloud")
                    for mid in ollama_cloud_models
                }
                config["provider"]["ollama-cloud"] = {"models": enriched_cloud}
                if local_ollama and args.preset not in ["plus", "pro"]:
                    config["provider"]["ollama"] = local_ollama
            elif local_ollama and args.preset not in ["plus", "pro"]:
                # No cloud models at all; local-only provider
                config["provider"]["ollama"] = local_ollama
            if include_anthropic and anthropic_models:
                enriched_anthropic = {
                    mid: build_model_entry(mid, models_dev_data, "anthropic")
                    for mid in anthropic_models
                }
                config["provider"]["anthropic"] = {"models": enriched_anthropic}
            if args.preset == "pro":
                config["provider"].pop("openai", None)
                config["provider"].pop("anthropic", None)
                if can_proxy_cloud:
                    config["disabled_providers"].extend(["openai", "anthropic"])
                else:
                    config["disabled_providers"].extend(
                        ["openai", "anthropic", "ollama"]
                    )
            elif args.preset == "plus":
                config["provider"].pop("anthropic", None)
                config["disabled_providers"].extend(
                    ["anthropic", "ollama-cloud", "ollama"]
                )

        if meridian_plugin_path:
            config["plugin"].append(meridian_plugin_path)

    server_config = build_opencode_server_config()
    if server_config:
        existing_server = config.get("server")
        if isinstance(existing_server, dict):
            existing_server.update(server_config)
        else:
            config["server"] = server_config

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

    # 3b. Generate and merge acpAgents (ACP-capable agent wrappers)
    acp_agents_source_path = os.path.join(
        configs_dir_path, "opencode", "acp-agents.json"
    )
    logger.info("Generating ACP agent wrappers...")
    try:
        acp_args = [
            sys.executable,
            os.path.join(SCRIPT_DIR, "configure-acp-agents.py"),
            "--preset",
            args.preset,
            "--no-backup",
        ]
        acp_result = subprocess.run(
            acp_args,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if acp_result.returncode != 0:
            stderr = (acp_result.stderr or acp_result.stdout or "").strip()
            logger.warning(
                f"ACP agent generation failed (exit {acp_result.returncode}); skipping merge"
                + (f": {stderr}" if stderr else "")
            )
        elif not os.path.exists(acp_agents_source_path):
            logger.warning(
                f"ACP agent generation completed but {acp_agents_source_path} was not found; skipping merge"
            )
        else:
            slim_output_path = os.path.join(config_dir_path, "oh-my-opencode-slim.json")
            try:
                with open(slim_output_path, "r", encoding="utf-8") as f:
                    slim_data = json.load(f)
                with open(acp_agents_source_path, "r", encoding="utf-8") as f:
                    acp_data = json.load(f)
                if isinstance(acp_data, dict) and isinstance(
                    acp_data.get("acpAgents"), dict
                ):
                    slim_data["acpAgents"] = acp_data["acpAgents"]
                    with open(slim_output_path, "w", encoding="utf-8") as f:
                        json.dump(slim_data, f, indent=2)
                        f.write("\n")
                    logger.info("acpAgents merged into oh-my-opencode-slim.json")
                else:
                    logger.warning(
                        f"ACP agent config at {acp_agents_source_path} did not contain acpAgents; skipping merge"
                    )
            except Exception as e:
                logger.warning(
                    f"Failed to merge acpAgents into oh-my-opencode-slim.json: {e}"
                )
    except Exception as e:
        logger.warning(f"Failed to configure ACP agents: {e}")

    # 4. Set active tier
    logger.info(f"Setting active tier to {args.preset}...")
    try:
        tier_args_list = build_tier_args(
            tier=args.preset,
            no_local_fallbacks=not with_local_ollama,
            local_fallback_preset=args.local_fallback_preset,
            local_fallback_placeholders=args.local_fallback_placeholder or None,
            local_fallback_roles=args.local_fallback_role or None,
        )
        tier_args = [
            sys.executable,
            os.path.join(SCRIPT_DIR, "configure-opencode-tier.py"),
        ] + tier_args_list
        subprocess.run(tier_args, check=True)
        logger.info(f"Active tier set to {args.preset}")
    except Exception as e:
        logger.critical(f"Failed to set active tier: {e}")
        sys.exit(1)

    # 5. Configure voice plugin (tui.json)
    logger.info("Configuring voice plugin...")
    try:
        voice_args = [
            sys.executable,
            os.path.join(SCRIPT_DIR, "configure-opencode-voice.py"),
            "--preset",
            args.preset,
            "--no-backup",
        ]
        subprocess.run(voice_args, check=True)
        logger.info("Voice plugin configured")
    except Exception as e:
        logger.warning(f"Failed to configure voice plugin: {e}")

    # 5b. Configure DCP TUI plugin (tui.json)
    logger.info("Configuring DCP TUI plugin...")
    try:
        dcp_args = [
            sys.executable,
            os.path.join(SCRIPT_DIR, "configure-opencode-dcp.py"),
            "--no-backup",
        ]
        subprocess.run(dcp_args, check=True)
        logger.info("DCP TUI plugin configured")
    except Exception as e:
        logger.warning(f"Failed to configure DCP TUI plugin: {e}")

    summary_lines = [
        "OpenCode configured!",
        "",
        f"Config written to: {config_dir_path}",
        "  • opencode.json (providers, MCP servers, plugins)",
        f"  • oh-my-opencode-slim.json (all presets, active: {args.preset})",
        "  • vibeguard.config.json (sensitive-string redaction)",
        "  • acp-agents.json (ACP-capable agent wrappers — see ~/.config/opencode/oh-my-opencode-slim.json)",
        "  • tui.json (voice + DCP TUI plugin config)",
        "",
        "To switch tiers:",
        "     configure-opencode-tier.py pro",
        "     configure-opencode-tier.py pro-plus",
        "     configure-opencode-tier.py pro-plus-anthropic",
        "     configure-opencode-tier.py plus",
        "     configure-opencode-tier.py anthropic",
        "     configure-opencode-tier.py local-pro",
        "     configure-opencode-tier.py local",
        "     configure-opencode-tier.py local-mini",
        "     configure-opencode-tier.py local-nano",
        "     configure-opencode-tier.py local-solo",
        "",
        "To regenerate without local ollama:",
        "     set DOTFILES_USE_LOCAL_OLLAMA=0 and re-run configure-opencode.py",
        "",
        "To add/update Meridian proxy plugin:",
        "     configure-meridian.py",
        "",
        "To configure voice plugin separately:",
        "     configure-opencode-voice.py --preset <tier>",
        "",
        "Configure script complete!",
    ]
    logger.info("\n".join(summary_lines))


if __name__ == "__main__":
    main()
