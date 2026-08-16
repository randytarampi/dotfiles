#!/usr/bin/env python3
"""Shared OpenCode configuration helpers.

Provides common utilities for working with oh-my-opencode-slim.json,
including tier name discovery, config path resolution, and other
shared logic used by multiple configure scripts.
"""

import json
import os
import re

# Recognized provider prefixes that map to opencode.json provider blocks.
# `ollama-cloud` is matched before `ollama` to avoid mis-parsing "ollama-cloud/...".
_PROVIDER_PREFIXES = ("openai", "anthropic", "ollama-cloud", "ollama")

# Matches "<provider>/<model>" model strings used throughout presets.
_PROVIDER_MODEL_RE = re.compile(
    r"^(ollama-cloud|ollama|openai|anthropic)/[A-Za-z0-9._:\-]+$"
)

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
    args = ["--preset", tier]
    if no_local_fallbacks:
        args.insert(0, "--no-local-fallbacks")
    if local_fallback_preset:
        args.extend(["--local-fallback-preset", local_fallback_preset])
    for p in local_fallback_placeholders or []:
        args.extend(["--local-fallback-placeholder", p])
    for r in local_fallback_roles or []:
        args.extend(["--local-fallback-role", r])
    return args


def _collect_provider_from_value(value, out):
    """Collect provider IDs from a single model-string value."""
    if isinstance(value, str):
        if value.startswith("_local:"):
            # _local:<category> placeholders resolve to the local ollama provider
            # at tier-switch time. The provider block required is "ollama".
            out.add("ollama")
            return
        m = _PROVIDER_MODEL_RE.match(value)
        if m:
            out.add(m.group(1))
            return
        # Bare provider/model without strict match — best-effort prefix scan.
        for prefix in _PROVIDER_PREFIXES:
            if value.startswith(prefix + "/"):
                out.add(prefix)
                return


def _collect_providers(obj, out):
    """Recursively walk a preset/fallback object collecting provider IDs."""
    if isinstance(obj, dict):
        for v in obj.values():
            _collect_providers(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_providers(v, out)
    else:
        _collect_provider_from_value(obj, out)


def get_preset_providers(preset_name, slim_json_path=None):
    """Return the set of provider IDs a preset references.

    Parses role models, council preset models, and fallback chains in
    oh-my-opencode-slim.json for the given preset/tier. `_local:*`
    placeholders resolve to ``"ollama"`` (the local Ollama provider).

    Args:
        preset_name: The preset/tier name (e.g. "anthropic", "pro-plus").
        slim_json_path: Optional explicit path to oh-my-opencode-slim.json.
            Defaults to the repo's configs/opencode/oh-my-opencode-slim.json.

    Returns:
        Set of provider ID strings, e.g. ``{"openai", "ollama-cloud"}`` or
        ``{"anthropic"}``. Empty set if the preset is unknown or references
        no recognizable provider strings.

    Raises:
        FileNotFoundError: If oh-my-opencode-slim.json is missing.
        json.JSONDecodeError: If the config file is invalid JSON.
    """
    source_path = slim_json_path or get_slim_config_path()
    with open(source_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    providers = set()

    # 1. Top-level presets[preset_name] — role -> {model, ...}
    top_preset = data.get("presets", {}).get(preset_name)
    if top_preset:
        _collect_providers(top_preset, providers)

    # 2. _tiers[preset_name] — council presets + fallback chains
    tier = data.get("_tiers", {}).get(preset_name, {})
    council = tier.get("council", {})
    for cp in council.get("presets", {}).values():
        _collect_providers(cp, providers)
    _collect_providers(tier.get("fallback", {}), providers)

    return providers


def get_fixer_model(preset_name, slim_json_path=None):
    """Return the fixer model string for a given preset/tier."""
    source_path = slim_json_path or get_slim_config_path()
    with open(source_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("presets", {}).get(preset_name, {}).get("fixer", {}).get("model")
