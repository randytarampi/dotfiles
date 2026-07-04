#!/usr/bin/env python3
# pyright: reportMissingImports=false
"""Configure SmallCode.

Writes tier-aware SmallCode env and TOML files for either the global user
config directory or a project-local .smallcode directory.
"""

import sys
import argparse
import json
import os

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
LIB_DIR = SCRIPT_DIR if SCRIPT_DIR.endswith("lib") else os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger
from constants import (
    check_ollama_daemon,
    get_meridian_base_url,
    get_ollama_local_base_url,
    get_provider_base_url,
    is_meridian_configured,
    MERIDIAN_HOST_ENV,
    MERIDIAN_PORT_ENV,
    MERIDIAN_DEFAULT_HOST,
    MERIDIAN_DEFAULT_PORT,
)
from env import load_env
from file_utils import write_text_file
from opencode_config import get_available_tiers, get_slim_config_path
from ai_models import strip_provider_prefix
from tier_resolve import resolve_roles_from_list, list_local_ollama_models

AVAILABLE_PRESETS = get_available_tiers()
LOCAL_PRESETS = {t for t in AVAILABLE_PRESETS if t.startswith("local")}
_SLIM_CONFIG_CACHE = None
_OLLAMA_DAEMON_CACHE = None


def is_truthy(value):
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def get_config_dir(mode):
    if mode == "project":
        return os.path.abspath(os.path.join(os.getcwd(), ".smallcode"))
    return os.path.join(os.path.expanduser("~"), ".config", "smallcode")


def load_slim_config():
    global _SLIM_CONFIG_CACHE
    if _SLIM_CONFIG_CACHE is None:
        with open(get_slim_config_path(), "r", encoding="utf-8") as handle:
            _SLIM_CONFIG_CACHE = json.load(handle)
    return _SLIM_CONFIG_CACHE


def get_ollama_cloud_capable():
    """Cached check for whether local Ollama can proxy cloud models."""
    global _OLLAMA_DAEMON_CACHE
    if _OLLAMA_DAEMON_CACHE is None:
        _OLLAMA_DAEMON_CACHE = check_ollama_daemon()
    return _OLLAMA_DAEMON_CACHE


def get_model_provider(model_name):
    _, can_proxy_cloud = get_ollama_cloud_capable()
    if model_name.startswith("ollama-cloud/"):
        if can_proxy_cloud:
            return "ollama"
        return "ollama-cloud"
    if model_name.startswith("anthropic/"):
        if is_meridian_configured():
            return "meridian"
        return "anthropic"
    if model_name.startswith("openai/"):
        return "openai"
    logger.warning(
        f"Unrecognized provider prefix in model '{model_name}'; defaulting to openai"
    )
    return "openai"


def get_model_base_url(model_name):
    if "/" not in model_name:
        return get_ollama_local_base_url()
    provider = get_model_provider(model_name)
    if model_name.startswith("ollama-cloud/"):
        _, can_proxy_cloud = get_ollama_cloud_capable()
        if can_proxy_cloud:
            return get_ollama_local_base_url()
    if provider == "meridian":
        return get_meridian_base_url()
    return get_provider_base_url(provider)


def get_effective_model_name(model_name):
    """Get the effective model name for API calls."""
    stripped = strip_provider_prefix(model_name)
    _, can_proxy_cloud = get_ollama_cloud_capable()
    if model_name.startswith("ollama-cloud/") and can_proxy_cloud:
        if not stripped.endswith(":cloud"):
            stripped = f"{stripped}:cloud"
    return stripped


def get_cloud_spec(preset):
    """Derive SmallCode spec from OpenCode preset in oh-my-opencode-slim.json.

    Maps OpenCode roles to SmallCode routing tiers:
      DEFAULT ← orchestrator
      FAST     ← librarian
      MEDIUM   ← fixer
      STRONG   ← oracle
    """
    config = load_slim_config()
    preset_config = config.get("presets", {}).get(preset)
    if not preset_config:
        raise ValueError(f"Unsupported cloud preset: {preset}")

    role_names = {
        "default_model": "orchestrator",
        "fast_model": "librarian",
        "medium_model": "fixer",
        "strong_model": "oracle",
    }
    role_models = {}
    for slot, role in role_names.items():
        role_config = preset_config.get(role)
        if not isinstance(role_config, dict) or not role_config.get("model"):
            raise ValueError(f"Preset '{preset}' is missing role '{role}'")
        role_models[slot] = role_config["model"]

    provider = get_model_provider(role_models["default_model"])
    escalation_provider = get_model_provider(role_models["strong_model"])
    escalation_keys = {
        "ollama-cloud": "OLLAMA_API_KEY",
        "ollama": "OLLAMA_API_KEY",
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "meridian": "MERIDIAN_API_KEY",
    }
    escalation_key = escalation_keys.get(escalation_provider)
    if not escalation_key:
        raise ValueError(
            f"Unsupported escalation provider for preset '{preset}': {escalation_provider}"
        )

    return {
        "provider": provider,
        "default_model": get_effective_model_name(role_models["default_model"]),
        "default_base_url": get_model_base_url(role_models["default_model"]),
        "fast_model": get_effective_model_name(role_models["fast_model"]),
        "fast_base_url": get_model_base_url(role_models["fast_model"]),
        "medium_model": get_effective_model_name(role_models["medium_model"]),
        "medium_base_url": get_model_base_url(role_models["medium_model"]),
        "strong_model": get_effective_model_name(role_models["strong_model"]),
        "strong_base_url": get_model_base_url(role_models["strong_model"]),
        "escalation_provider": escalation_provider,
        "escalation_model": get_effective_model_name(role_models["strong_model"]),
        "escalation_key": escalation_key,
    }


def get_local_spec(preset=None):
    """Return SmallCode spec template for local tiers.

    Derives model category slots from the OpenCode preset when available,
    falling back to defaults. Model slots use _local:<category> placeholders
    that are resolved later by resolve_local_models() + apply_local_overrides().
    """
    # Default category mapping (matches local-pro/local presets)
    slot_categories = {
        "default_model": "code-gen",
        "fast_model": "lightweight",
        "medium_model": "reasoning",
        "strong_model": "reasoning",
    }

    # Derive from OpenCode preset roles when available
    if preset:
        try:
            config = load_slim_config()
            preset_config = config.get("presets", {}).get(preset, {})
            role_to_slot = {
                "orchestrator": "default_model",
                "librarian": "fast_model",
                "fixer": "medium_model",
                "oracle": "strong_model",
            }
            for role, slot in role_to_slot.items():
                role_config = preset_config.get(role)
                if isinstance(role_config, dict) and role_config.get("model"):
                    model = role_config["model"]
                    if model.startswith("_local:"):
                        category = model[len("_local:") :]
                        slot_categories[slot] = category
        except Exception:
            pass  # Fall back to defaults

    return {
        "provider": "openai",  # Ollama's OpenAI-compatible API endpoint
        "default_model": f"_local:{slot_categories['default_model']}",
        "fast_model": f"_local:{slot_categories['fast_model']}",
        "medium_model": f"_local:{slot_categories['medium_model']}",
        "strong_model": f"_local:{slot_categories['strong_model']}",
        "default_base_url": get_ollama_local_base_url(),
        "fast_base_url": get_ollama_local_base_url(),
        "medium_base_url": get_ollama_local_base_url(),
        "strong_base_url": get_ollama_local_base_url(),
        "escalation_provider": None,
        "escalation_model": None,
        "escalation_key": None,
    }


def build_spec(preset, override_base_url=None):
    if preset in LOCAL_PRESETS:
        return get_local_spec(preset)

    spec = get_cloud_spec(preset)
    primary_override = (
        override_base_url or os.environ.get("SMALLCODE_BASE_URL", "").strip() or None
    )
    if primary_override:
        for key in [
            "default_base_url",
            "fast_base_url",
            "medium_base_url",
            "strong_base_url",
        ]:
            spec[key] = primary_override

    return spec


def build_env_content(preset, spec):
    provider = spec["provider"]
    if provider == "meridian":
        provider = "openai"

    lines = [
        "# SmallCode configuration — generated by configure-smallcode.py",
        f"# Do not edit manually; regenerate with: configure-smallcode.py --preset {preset}",
        f"SMALLCODE_MODEL_DEFAULT={spec['default_model']}",
        f"SMALLCODE_BASE_URL_DEFAULT={spec['default_base_url']}",
        f"SMALLCODE_PROVIDER={provider}",
        f"SMALLCODE_MODEL_FAST={spec['fast_model']}",
    ]

    if spec["fast_base_url"] != spec["default_base_url"]:
        lines.append(f"SMALLCODE_BASE_URL_FAST={spec['fast_base_url']}")

    lines.append(f"SMALLCODE_MODEL_MEDIUM={spec['medium_model']}")
    if spec["medium_base_url"] != spec["default_base_url"]:
        lines.append(f"SMALLCODE_BASE_URL_MEDIUM={spec['medium_base_url']}")

    lines.append(f"SMALLCODE_MODEL_STRONG={spec['strong_model']}")
    if spec["strong_base_url"] != spec["default_base_url"]:
        lines.append(f"SMALLCODE_BASE_URL_STRONG={spec['strong_base_url']}")

    lines.extend(
        [
            "SMALLCODE_CONTEXT_BUDGET=67",
            "SMALLCODE_CONTEXT_WINDOW=128000",
            "SMALLCODE_MODEL_TIMEOUT=300",
            "SMALLCODE_ESCALATION_CONFIRM=true",
            "SMALLCODE_AUTO_COMMIT=false",
            "SMALLCODE_THEME=dark",
            "",
        ]
    )
    return "\n".join(lines)


def build_toml_content(preset, spec):
    """Build config.toml content.

    SmallCode reads config.model.name from SMALLCODE_MODEL env or TOML [model].
    The .env file sets SMALLCODE_MODEL_DEFAULT (a tier var) but NOT SMALLCODE_MODEL,
    so the TOML [model] section is required for the primary model to be recognized.
    Without it, smallcode shows "no model configured" and refuses to start.
    """
    provider = spec["provider"]
    if provider == "meridian":
        provider = "openai"

    lines = [
        "# SmallCode configuration — generated by configure-smallcode.py",
        f"# Do not edit manually; regenerate with: configure-smallcode.py --preset {preset}",
        "",
        "[model]",
        f'provider = "{provider}"',
        f'name = "{spec["default_model"]}"',
        f'baseUrl = "{spec["default_base_url"]}"',
        "",
    ]

    escalation_key = spec.get("escalation_key")
    if escalation_key and os.environ.get(escalation_key):
        escalation_provider = spec["escalation_provider"]
        if escalation_provider == "meridian":
            lines.extend(
                [
                    "[escalation]",
                    'provider = "openai"',
                    f'baseUrl = "{get_meridian_base_url()}"',
                    f'model = "{spec["escalation_model"]}"',
                    "",
                ]
            )
        else:
            escalation_lines = [
                "[escalation]",
                f'provider = "{spec["escalation_provider"]}"',
            ]
            # Write baseUrl for ollama provider (handles non-default OLLAMA_HOST)
            if spec["escalation_provider"] == "ollama":
                escalation_lines.append(f'baseUrl = "{get_ollama_local_base_url()}"')
            escalation_lines.extend(
                [
                    f'model = "{spec["escalation_model"]}"',
                    "",
                ]
            )
            lines.extend(escalation_lines)

    return "\n".join(lines)


def configure_mcp(config_dir_path, no_backup):
    """Write SmallCode MCP client config.

    SmallCode's mcp.json uses the mcpServers format to define which MCP
    servers SmallCode connects to. Since SmallCode isn't registered as a
    tool in global-mcps.json (it's an MCP *server* for other tools, not
    a client managed by configure-mcp-tool.py), we write a minimal
    config directly. Users can add servers manually or via
    configure-mcps.py in the future.
    """
    mcp_path = os.path.join(config_dir_path, "mcp.json")
    mcp_content = json.dumps({"mcpServers": {}}, indent=2) + "\n"

    try:
        write_text_file(mcp_path, mcp_content, backup=not no_backup)
        logger.info(f"mcp.json written to {mcp_path}")
        return True
    except Exception as exc:
        logger.warning(f"Failed to write SmallCode MCP config: {exc}")
        return False


def local_summary_lines(resolved_roles):
    """Generate summary lines from resolved local roles.

    resolved_roles is a dict of {category: "ollama/model_name"} strings
    from tier_resolve.resolve_roles_from_list().
    """
    if not isinstance(resolved_roles, dict):
        return []

    lines = []
    for category in ["code-gen", "lightweight", "reasoning", "vision", "solo"]:
        model_name = resolved_roles.get(category)
        if model_name:
            display_name = strip_provider_prefix(model_name)
            lines.append(f"  • {category}={display_name}")
    return lines


def escalation_status(spec):
    key_name = spec.get("escalation_key")
    if key_name and os.environ.get(key_name):
        return "enabled"
    return "skipped"


def resolve_local_models(
    preset,
    no_local_fallbacks,
    local_fallback_preset,
    local_fallback_placeholders,
    local_fallback_roles,
    min_reasoning_embedding: int = 0,
):
    """Resolve local Ollama model placeholders for SmallCode tiers.

    Uses tier_resolve.resolve_roles_from_list for role
    resolution. Returns a dict mapping category names to resolved model
    name strings, or an empty dict if local fallbacks are disabled or
    unavailable.
    """
    if no_local_fallbacks and not preset.startswith("local"):
        return {}

    try:
        local_models = list_local_ollama_models()
        if local_models:
            roles = resolve_roles_from_list(
                local_models, min_reasoning_embedding=min_reasoning_embedding
            )
            return roles
        logger.info("No local Ollama models discovered")
        return {}
    except Exception as exc:
        logger.warning(
            f"Failed to resolve local roles via resolve_roles_from_list: {exc}"
        )

    logger.warning("Local Ollama model resolution failed — no local models available")
    return {}


def apply_local_overrides(spec, resolved_roles, preset):
    """Apply resolved local model overrides to a spec dict.

    Maps local category placeholders to SmallCode tier slots:
      _local:code-gen  → DEFAULT
      _local:lightweight → FAST
      _local:reasoning → MEDIUM, STRONG
      _local:vision    → FAST fallback
      _local:solo      → DEFAULT, FAST, MEDIUM, STRONG (local-solo only)

    resolved_roles is a dict of {category: "ollama/model_name"} strings
    from tier_resolve.resolve_roles_from_list().
    """
    # Map SmallCode slots to local categories
    if preset == "local-solo":
        slot_categories = {
            "default_model": "solo",
            "fast_model": "solo",
            "medium_model": "solo",
            "strong_model": "solo",
        }
    else:
        slot_categories = {
            "default_model": "code-gen",
            "fast_model": "lightweight",
            "medium_model": "reasoning",
            "strong_model": "reasoning",
        }

    for slot, category in slot_categories.items():
        placeholder = f"_local:{category}"
        current = spec.get(slot, "")
        if current == placeholder and isinstance(resolved_roles, dict):
            model_name = resolved_roles.get(category)
            if model_name:
                # Keep provider-aware effective names for SmallCode config values
                spec[slot] = get_effective_model_name(model_name)

    # Fallback: if reasoning category has no resolved model, try code-gen
    for slot in ["medium_model", "strong_model"]:
        val = spec.get(slot, "")
        if val.startswith("_local:reasoning") and not resolved_roles.get("reasoning"):
            code_gen_model = resolved_roles.get("code-gen")
            if code_gen_model:
                logger.info(
                    f"No reasoning model found for {slot}; "
                    f"falling back to code-gen: {get_effective_model_name(code_gen_model)}"
                )
                spec[slot] = get_effective_model_name(code_gen_model)

    # Also resolve any remaining _local:* placeholders
    for slot in ["default_model", "fast_model", "medium_model", "strong_model"]:
        val = spec.get(slot, "")
        if val.startswith("_local:"):
            category = val[len("_local:") :]
            if isinstance(resolved_roles, dict):
                model_name = resolved_roles.get(category)
                if model_name:
                    spec[slot] = get_effective_model_name(model_name)

    return spec


def main():
    parser = argparse.ArgumentParser(
        description="Configure SmallCode environment and escalation settings."
    )
    parser.add_argument(
        "--preset",
        required=True,
        choices=AVAILABLE_PRESETS,
        help="Active tier preset",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not create .bak files for existing configs",
    )
    parser.add_argument(
        "--mode",
        default="global",
        choices=["global", "project"],
        help="Write global config or project-local .smallcode config",
    )
    parser.add_argument(
        "--no-mcp",
        action="store_true",
        help="Skip MCP server configuration",
    )
    parser.add_argument(
        "--no-local-fallbacks",
        action="store_true",
        help="Skip local Ollama model discovery and appending",
    )
    parser.add_argument(
        "--local-fallback-preset",
        default=None,
        help="Which local tier's placeholder pattern to use for local fallbacks (default: local)",
    )
    parser.add_argument(
        "--local-fallback-placeholder",
        action="append",
        default=[],
        help="Override local category placeholder (format: category=replacement, e.g. reasoning=code-gen). May be specified multiple times.",
    )
    parser.add_argument(
        "--local-fallback-role",
        action="append",
        default=[],
        help="Override which model fills a specific role (format: role=model, e.g. observer=ollama/qwen3.5:9b-mlx). May be specified multiple times.",
    )
    parser.add_argument(
        "--ollama-base-url",
        default=None,
        help="Custom Ollama base URL (default: http://localhost:11434/v1)",
    )
    parser.add_argument(
        "--min-reasoning-embedding",
        type=int,
        default=int(os.environ.get("DOTFILES_MIN_REASONING_EMBEDDING", "0") or "0"),
        help="Minimum embedding_length for reasoning/solo roles (0 = disabled). "
        "Env: DOTFILES_MIN_REASONING_EMBEDDING (default 0).",
    )
    args = parser.parse_args()

    if not load_env():
        logger.warning(
            "~/.env not found or unreadable; continuing with process environment"
        )

    config_dir_path = get_config_dir(args.mode)
    os.makedirs(config_dir_path, exist_ok=True)

    spec = build_spec(args.preset, override_base_url=args.ollama_base_url)

    # Resolve local Ollama models for local tiers or fallback appending
    resolved_roles = None
    if not args.no_local_fallbacks or args.preset in LOCAL_PRESETS:
        resolved_roles = resolve_local_models(
            args.preset,
            args.no_local_fallbacks,
            args.local_fallback_preset,
            args.local_fallback_placeholder,
            args.local_fallback_role,
            min_reasoning_embedding=args.min_reasoning_embedding,
        )
        if resolved_roles and args.preset in LOCAL_PRESETS:
            spec = apply_local_overrides(spec, resolved_roles, args.preset)

    env_path = os.path.join(config_dir_path, ".env")
    # SmallCode reads global config from ~/.config/smallcode/config.toml (not smallcode.toml).
    # Project-local mode uses .smallcode/config.toml. Both paths use "config.toml".
    toml_path = os.path.join(config_dir_path, "config.toml")

    try:
        write_text_file(
            env_path, build_env_content(args.preset, spec), backup=not args.no_backup
        )
        logger.info(f".env written to {env_path}")
    except Exception as exc:
        logger.critical(f"Failed to write SmallCode env file: {exc}")
        sys.exit(1)

    try:
        write_text_file(
            toml_path, build_toml_content(args.preset, spec), backup=not args.no_backup
        )
        logger.info(f"config.toml written to {toml_path} ({escalation_status(spec)})")
    except Exception as exc:
        logger.critical(f"Failed to write SmallCode TOML file: {exc}")
        sys.exit(1)

    mcp_written = False
    if not args.no_mcp:
        mcp_written = configure_mcp(config_dir_path, args.no_backup)

    summary_lines = [
        "SmallCode configured!",
        "",
        f"Config written to: {config_dir_path}",
        f"  • .env: default={spec['default_model']}, fast={spec['fast_model']}, medium={spec['medium_model']}, strong={spec['strong_model']}",
        f"  • provider: {spec['provider']}",
        f"  • config.toml: escalation {escalation_status(spec)}",
    ]

    if args.preset in LOCAL_PRESETS:
        local_lines = local_summary_lines(resolved_roles)
        if local_lines:
            summary_lines.append("  • resolved local models:")
            summary_lines.extend(local_lines)
        elif is_truthy(os.environ.get("DOTFILES_USE_LOCAL_OLLAMA", "true")):
            summary_lines.append(
                "  • local Ollama discovery enabled, but no models were resolved"
            )

    if args.no_local_fallbacks:
        summary_lines.append("  • local fallbacks: disabled")
    elif resolved_roles:
        summary_lines.append("  • local fallbacks: enabled")

    if args.no_mcp:
        summary_lines.append("  • mcp.json: skipped (--no-mcp)")
    else:
        summary_lines.append(
            "  • mcp.json: written" if mcp_written else "  • mcp.json: failed to write"
        )

    summary_lines.extend(
        [
            "",
            "SmallCode configuration complete!",
        ]
    )
    logger.info("\n".join(summary_lines))


if __name__ == "__main__":
    main()
