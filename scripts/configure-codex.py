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

try:
    import tomllib
except ImportError:  # Python 3.10 compatibility
    import tomli as tomllib

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = SCRIPT_DIR if SCRIPT_DIR.endswith("lib") else os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger
import ai_models
from cli_helpers import add_common_args
from file_utils import backup_file, write_text_file
import tier_registry
from discover_models import list_local_ollama_models
from env import load_env
from local_engines import (
    active_engine_pools,
    local_endpoint_for,
    resolve_engine,
    resolve_local_winner,
)

PROFILES_START = "# BEGIN DOTFILES MANAGED PROFILES"
PROFILES_END = "# END DOTFILES MANAGED PROFILES"
DEFAULT_OLLAMA_CLOUD_MODEL = "glm-5.3-flash"

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


def _model_parts(model_ref):
    return model_ref.split("/", 1) if "/" in model_ref else ("ollama", model_ref)


def resolve_local_model():
    """Resolve the best chat-capable model from the combined local pool."""
    models = [model for pool in active_engine_pools().values() for model in pool]
    return resolve_local_winner(models, "openai") or ""


def build_profiles_config(ollama_model, local_model=""):
    provider, model = _model_parts(local_model) if local_model else ("ollama", "local")
    engine = resolve_engine(provider)
    profile_provider = engine["profile_provider"] if engine else "ollama"
    local_profile = (
        f'\n[profiles.local]\nmodel = "{model}"\n'
        f'model_provider = "{profile_provider}"\n'
    )
    litellm_profile = (
        '\n[profiles.litellm]\nmodel = "openai/gpt-6-luna"\n'
        'model_provider = "litellm"\n'
        if build_litellm_provider_config()
        else ""
    )
    return f"""# BEGIN DOTFILES MANAGED PROFILES
[profiles.meridian]
model = "claude-sonnet-5-5"
model_provider = "meridian"
model_reasoning_effort = "high"

[profiles.ollama-cloud]
model = "ollama-cloud/{ollama_model}"
model_provider = "ollama-cloud"
{litellm_profile}

[profiles.copilot]
model = "copilot-model-id"
model_provider = "github-copilot"
{local_profile}# END DOTFILES MANAGED PROFILES
"""


def resolve_ollama_cloud_model():
    """Resolve the configured tier's Ollama Cloud model without failing setup."""
    try:
        slim_path = os.path.join(
            SCRIPT_DIR, "..", "configs", "opencode", "oh-my-opencode-slim.json"
        )
        registry = tier_registry.load_registry(slim_path)
        tier = os.environ.get("DOTFILES_OPENCODE_TIER") or registry["preset"]
        preset = tier_registry.get_preset(registry, tier)
        original_model = preset["orchestrator"]["model"]
        if not isinstance(original_model, str) or not original_model.startswith(
            "ollama-cloud/"
        ):
            note = (
                f"orchestrator model for tier {tier} is not ollama-cloud "
                f"({original_model}) — using default {DEFAULT_OLLAMA_CLOUD_MODEL}"
            )
            logger.warning(note)
            return DEFAULT_OLLAMA_CLOUD_MODEL, note
        model = ai_models.strip_provider_prefix(original_model)
        note = f"derived from tier {tier} ({original_model})"
        return model, note
    except Exception as exc:
        note = (
            f"could not derive Ollama Cloud model ({exc}) — using default "
            f"{DEFAULT_OLLAMA_CLOUD_MODEL}"
        )
        logger.warning(note)
        return DEFAULT_OLLAMA_CLOUD_MODEL, note


COPILOT_PROVIDER_CONFIG = """[model_providers.github-copilot]
name = "GitHub Copilot"
base_url = "https://api.githubcopilot.com/v1"
env_key = "GITHUB_TOKEN"
wire_api = "responses"
"""


LITELLM_PROVIDER_START = "# BEGIN DOTFILES MANAGED PROVIDER litellm"
LITELLM_PROVIDER_END = "# END DOTFILES MANAGED PROVIDER litellm"
LITELLM_PROVIDER_TEMPLATE = """# BEGIN DOTFILES MANAGED PROVIDER litellm
[model_providers.litellm]
name = "LiteLLM Proxy"
base_url = "{base_url}"
env_key = "LITELLM_CODEX_KEY"
wire_api = "responses"
# END DOTFILES MANAGED PROVIDER litellm
"""


def build_litellm_provider_config():
    """Build the litellm provider block when setup gate and proxy are active."""
    if (
        os.environ.get("DOTFILES_RUN_CODEX_SETUP", "0") != "1"
        or os.environ.get("DOTFILES_USE_LITELLM_PROXY", "0") != "1"
    ):
        return ""
    port = os.environ.get("LITELLM_PORT", "4000")
    return LITELLM_PROVIDER_TEMPLATE.format(base_url=f"http://127.0.0.1:{port}/v1")


def build_provider_config(local_model=""):
    config = BASE_PROVIDER_CONFIG
    providers = set(active_engine_pools())
    if local_model:
        providers.add(_model_parts(local_model)[0])
    for provider in sorted(providers):
        engine = resolve_engine(provider)
        endpoint = local_endpoint_for(provider, "openai") if engine else None
        if not engine or not endpoint or not engine["provider_config"]:
            continue
        base_url, api_key_env = endpoint
        profile_provider = engine["profile_provider"]
        config += (
            f'\n[model_providers.{profile_provider}]\nname = "Local {provider}"\n'
            f'base_url = "{base_url}"\nwire_api = "responses"\n'
        )
        if api_key_env and os.environ.get(api_key_env, "").strip():
            config += f'env_key = "{api_key_env}"\n'
    if os.environ.get("GITHUB_TOKEN", "").strip():
        config += "\n" + COPILOT_PROVIDER_CONFIG
    litellm_config = build_litellm_provider_config()
    if litellm_config:
        config += "\n" + litellm_config
    return config


def strip_managed_profiles(content):
    return strip_owned_block(content, PROFILES_START, PROFILES_END, "profiles")


def strip_owned_block(content, start, end, table):
    lines = content.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.strip() == start]
    ends = [i for i, line in enumerate(lines) if line.strip() == end]
    if not starts and not ends:
        return content
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise ValueError(f"ambiguous managed {table} markers")
    owned = tomllib.loads("".join(lines[starts[0] + 1 : ends[0]]))
    if set(owned) != {table}:
        raise ValueError(f"managed {table} markers enclose unrelated settings")
    return "".join(lines[: starts[0]] + lines[ends[0] + 1 :])


def reconcile_litellm_provider(content, enabled):
    """Reconcile the marked provider without modifying unowned TOML."""
    start_marker, end_marker = LITELLM_PROVIDER_START, LITELLM_PROVIDER_END
    lines = content.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.strip() == start_marker]
    ends = [i for i, line in enumerate(lines) if line.strip() == end_marker]
    if starts or ends:
        if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
            raise ValueError("ambiguous managed LiteLLM provider markers")
        try:
            marked = tomllib.loads("".join(lines[starts[0] + 1 : ends[0]]))
        except tomllib.TOMLDecodeError as exc:
            raise ValueError("invalid managed LiteLLM provider block") from exc
        if set(marked) != {"model_providers"} or set(marked["model_providers"]) != {
            "litellm"
        }:
            raise ValueError("managed markers do not enclose only the LiteLLM provider")
        content = "".join(lines[: starts[0]] + lines[ends[0] + 1 :])
    try:
        parsed = tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"refusing invalid existing Codex TOML: {exc}") from exc
    if "litellm" in parsed.get("model_providers", {}):
        raise ValueError("refusing to overwrite unmanaged model_providers.litellm")
    if not enabled:
        return content
    block = build_litellm_provider_config().rstrip()
    return content.rstrip() + ("\n\n" if content.strip() else "") + block + "\n"


def build_summary_lines(config, config_path, ollama_cloud_model, note, local_model):
    """Summarize the provider/profile configuration that was actually written."""
    lines = ["Codex providers configured!", "", f"Configuration: {config_path}"]
    for match in re.finditer(
        r"\[model_providers\.([^\]]+)\]\n(.*?)(?=\n\[|\Z)", config, re.DOTALL
    ):
        section = match.group(2)
        base_url = re.search(r'^base_url = "([^"]+)"', section, re.MULTILINE)
        wire_api = re.search(r'^wire_api = "([^"]+)"', section, re.MULTILINE)
        lines.append(
            f"  • {match.group(1)}: {base_url.group(1) if base_url else 'built-in'}"
            f" — {wire_api.group(1) if wire_api else 'native'}"
        )
    profiles = re.findall(r"\[profiles\.([^\]]+)\]", config)
    lines.append(f"  • profiles: {', '.join(profiles)}")
    parsed = tomllib.loads(config)
    root_provider = parsed.get("model_provider")
    lines.append(f"  • root provider: {root_provider or 'unset (Codex default)'}")
    profiles = parsed.get("profiles", {})
    if "litellm" in profiles:
        lines.append("  • opt-in profile: litellm")
        lines.append(
            "  • runtime profile selection: unknown (not read from configuration)"
        )
    if "litellm" in parsed.get("model_providers", {}) and root_provider != "litellm":
        lines.append(
            "  • note: model_providers.litellm is managed but not the root default; "
            'set model_provider = "litellm" manually to route the default through '
            "the gateway (owner-controlled switch)"
        )
    provider, model = _model_parts(local_model) if local_model else ("ollama", "none")
    lines.append(f"  • local profile: {model} via {provider}")
    lines.extend(
        [
            f"  • ollama-cloud model: {ollama_cloud_model} ({note})",
            "",
            "Switch providers at runtime:",
            "  codex -c model_provider=meridian -m claude-sonnet-5-5",
            f"  codex -c model_provider=ollama-cloud -m {ollama_cloud_model}",
            f"  codex -c model_provider={provider} -m {model}",
            "",
            "Configure script complete!",
        ]
    )
    return lines


def main():
    parser = argparse.ArgumentParser(
        description="Configure model providers in the Codex CLI configuration."
    )
    add_common_args(parser, no_backup=True)
    args = parser.parse_args()

    # Load ~/.env so standalone runs see gate/endpoint vars (OMLX_* etc.);
    # inside make deploy the parent environment already carries them.
    load_env()

    ollama_cloud_model, ollama_cloud_model_note = resolve_ollama_cloud_model()
    local_model = resolve_local_model()

    config_dir = os.path.expanduser("~/.codex")
    config_path = os.path.join(config_dir, "config.toml")

    try:
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as config_file:
                content = config_file.read()
        else:
            content = ""
        original_content = content

        # Remove only the marked block before legacy provider cleanup.
        content = reconcile_litellm_provider(content, False)
        provider_start = "# BEGIN DOTFILES MANAGED PROVIDERS"
        provider_end = "# END DOTFILES MANAGED PROVIDERS"
        content = strip_owned_block(
            content, provider_start, provider_end, "model_providers"
        )
        content = strip_managed_profiles(content)
        new_content = content.rstrip()
        if new_content:
            new_content += "\n\n"
        base_providers = build_provider_config(local_model)
        litellm_config = build_litellm_provider_config()
        if litellm_config:
            content = reconcile_litellm_provider(content, True)
            base_providers = base_providers.replace(litellm_config, "")
            new_content = content.rstrip()
            if new_content:
                new_content += "\n\n"
        generated_profiles = build_profiles_config(ollama_cloud_model, local_model)
        existing = tomllib.loads(content)
        for table, generated in (
            ("model_providers", base_providers),
            ("profiles", generated_profiles),
        ):
            collisions = set(existing.get(table, {})) & set(
                tomllib.loads(generated).get(table, {})
            )
            if collisions:
                sections = ", ".join(f"[{table}.{name}]" for name in sorted(collisions))
                raise ValueError(
                    f"unmanaged {table} collision: {sections}. Back up config.toml, "
                    "review only these sections, then manually remove or rename "
                    "approved legacy sections and rerun. Preserve ambiguous sections; "
                    "no automatic migration was performed."
                )
        new_content += f"{provider_start}\n{base_providers.rstrip()}\n{provider_end}"
        # Provider composition appends LiteLLM once, after legacy cleanup.
        new_content += "\n\n" + generated_profiles.rstrip()
        tomllib.loads(new_content)

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
    except (OSError, ValueError) as exc:
        logger.critical(f"Failed to update Codex configuration: {exc}")
        sys.exit(1)

    summary_lines = build_summary_lines(
        new_content,
        config_path,
        ollama_cloud_model,
        ollama_cloud_model_note,
        local_model,
    )
    logger.info("\n".join(summary_lines))


if __name__ == "__main__":
    main()
