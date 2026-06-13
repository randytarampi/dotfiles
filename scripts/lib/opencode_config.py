#!/usr/bin/env python3
"""Shared OpenCode configuration helpers.

Provides common utilities for working with oh-my-opencode-slim.json,
including tier name discovery, config path resolution, and other
shared logic used by multiple configure scripts.
"""

import json
import os

# Path to the project configs directory (relative to this lib module)
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_CONFIGS_OPENDCODE_DIR = os.path.join(_PROJECT_ROOT, "configs", "opencode")


def get_configs_dir() -> str:
    """Return the path to configs/opencode/ directory."""
    return _CONFIGS_OPENDCODE_DIR


def get_slim_config_path() -> str:
    """Return the path to oh-my-opencode-slim.json."""
    return os.path.join(_CONFIGS_OPENDCODE_DIR, "oh-my-opencode-slim.json")


def get_available_tiers() -> list:
    """Load available tier names from oh-my-opencode-slim.json _tiers keys.

    Raises:
        FileNotFoundError: If oh-my-opencode-slim.json is missing.
        json.JSONDecodeError: If the config file is invalid JSON.
    """
    source_path = get_slim_config_path()
    with open(source_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    tiers = list(data.get("_tiers", {}).keys())
    if not tiers:
        raise ValueError("No tiers found in oh-my-opencode-slim.json _tiers")
    return tiers


def build_tier_args(
    tier: str,
    no_local_fallbacks: bool = False,
    local_fallback_preset: str = None,
    local_fallback_placeholders: list = None,
    local_fallback_roles: list = None,
) -> list:
    """Build argument list for configure-opencode-tier.py invocation.

    Args:
        tier: The tier name to switch to.
        no_local_fallbacks: If True, add --no-local-fallbacks flag.
        local_fallback_preset: If set, add --local-fallback-preset with this value.
        local_fallback_placeholders: List of category=model overrides
            (e.g. ["vision=ollama/gemma4:e4b"]).
        local_fallback_roles: List of role=model overrides
            (e.g. ["observer=ollama/qwen3.5:9b-mlx"]).

    Returns:
        List of CLI argument strings for configure-opencode-tier.py.
    """
    args = [tier]
    if no_local_fallbacks:
        args.insert(0, "--no-local-fallbacks")
    if local_fallback_preset:
        args.extend(["--local-fallback-preset", local_fallback_preset])
    for p in local_fallback_placeholders or []:
        args.extend(["--local-fallback-placeholder", p])
    for r in local_fallback_roles or []:
        args.extend(["--local-fallback-role", r])
    return args
