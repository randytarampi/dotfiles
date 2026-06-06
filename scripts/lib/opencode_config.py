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
