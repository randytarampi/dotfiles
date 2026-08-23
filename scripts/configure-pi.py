#!/usr/bin/env python3
"""Generate Pi agent configuration from the shared OpenCode tier registry."""

import argparse, json, os, sys
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(SCRIPT_DIR, "lib"))
import logger
from cli_helpers import add_common_args, add_local_fallback_args
from constants import (
    BASE_URLS,
    check_ollama_daemon,
    get_meridian_base_url,
    get_ollama_local_base_url,
)
from discover_models import list_local_ollama_models
from file_utils import backup_file, write_text_file
from opencode_config import get_available_tiers
from tier_resolve import resolve_roles_from_list

ROOT = Path(SCRIPT_DIR).parent
SLIM = ROOT / "configs/opencode/oh-my-opencode-slim.json"
ROLE_MAP = {
    "orchestrator": "delegate",
    "oracle": "oracle",
    "librarian": "researcher",
    "explorer": "scout",
    "designer": "custom",
    "fixer": "worker",
    "observer": "reviewer",
    "council": "workflowScript",
}


def pi_model(value, local):
    if not isinstance(value, str):
        return value
    if value.startswith("_local:"):
        return local.get(value[7:], local.get("code-gen", "ollama/qwen3-coder"))
    return value


def model_entry(model_id, name=None, local=False):
    # Read context window from OLLAMA_CONTEXT_LENGTH (matches the Ollama daemon's
    # KV cache sizing). Falls back to 128000 if unset. Keeping pi and Ollama in
    # sync avoids reserving VRAM that pi can never use.
    context_window = 128000
    env_ctx = os.environ.get("OLLAMA_CONTEXT_LENGTH", "")
    if env_ctx.isdigit():
        context_window = int(env_ctx)
    return {
        "id": model_id,
        "name": name or model_id,
        "reasoning": True,
        "input": ["text"],
        "contextWindow": context_window,
        "maxTokens": 32000,
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        **(
            {
                "compat": {
                    "supportsDeveloperRole": False,
                    "supportsReasoningEffort": False,
                }
            }
            if local
            else {}
        ),
    }


def seed_plugin_configs(dry_run=False, mode="global"):
    """Seed default rpiv-* plugin config files. Non-destructive: skips existing."""
    if os.environ.get("DOTFILES_RUN_PI_SETUP", "0") != "1":
        return
    # Only seed global configs in global mode — project mode should not
    # write to the user's ~/.config directory.
    if mode != "global":
        return
    config_dir = Path(
        os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    )
    seeds = {
        "rpiv-todo/config.json": {"maxWidgetLines": 8, "collapseKey": "alt+t"},
        "rpiv-ask-user-question/config.json": {"collapseKey": "alt+o"},
        "rpiv-voice/voice.json": {
            "hallucinationFilterEnabled": True,
            "equalizerEnabled": False,
        },
        "rpiv-i18n/locale.json": {"locale": "en"},
    }
    for rel_path, content in seeds.items():
        target = config_dir / rel_path
        if target.exists():
            continue
        if dry_run:
            logger.info("Would seed plugin config: %s", target)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as f:
            json.dump(content, f, indent=2)
            f.write("\n")
        logger.info("Seeded plugin config: %s", target)


def main():
    p = argparse.ArgumentParser(
        description="Configure Pi from the shared AI tier registry", allow_abbrev=False
    )
    add_common_args(p)
    add_local_fallback_args(p)
    p.add_argument("--mode", choices=["global", "project"], default="global")
    p.add_argument("--preset", choices=get_available_tiers())
    p.add_argument("--no-mcp", action="store_true")
    p.add_argument("--no-local-fallbacks", action="store_true")
    p.add_argument("--ollama-base-url")
    args = p.parse_args()
    with SLIM.open(encoding="utf-8") as f:
        data = json.load(f)
    preset = args.preset or data.get("preset", "pro-plus")
    roles = data.get("presets", {}).get(preset, {})
    local_models = list_local_ollama_models()
    local = resolve_roles_from_list(local_models) if local_models else {}
    if not local:
        local = {
            "code-gen": "ollama/qwen3-coder",
            "reasoning": "ollama/qwen3-coder",
            "lightweight": "ollama/qwen3-coder",
        }

    def resolve(v):
        return pi_model(v, local)

    default = resolve(
        roles.get("orchestrator", {}).get("model", "anthropic/claude-sonnet-4-20250514")
    )
    provider, _, default_model = default.partition("/")
    settings = {
        "defaultProvider": provider,
        "defaultModel": default_model or default,
        "defaultThinkingLevel": roles.get("orchestrator", {}).get("variant", "medium"),
        "theme": os.environ.get("PI_THEME", "dark"),
        "compaction": {
            "enabled": True,
            "reserveTokens": 16384,
            "keepRecentTokens": 20000,
        },
        "retry": {
            "enabled": True,
            "maxRetries": 3,
        },
        # Override Pi's DEFAULT_HTTP_IDLE_TIMEOUT_MS (300000ms / 5 min) which
        # governs both the Undici body/headers idle timeout and the fallback
        # provider timeout. 15 min covers cold 27B Ollama loads + long generation
        # while keeping failure-detection latency reasonable. The SDK defaults
        # (OpenAI/Anthropic: 600000ms / 10 min) apply for retry.provider.timeoutMs.
        "httpIdleTimeoutMs": 900000,
        "enableInstallTelemetry": False,
        "enableAnalytics": False,
        "enabledModels": [],  # populated after providers are built
        "packages": [
            "pi-skills",
            "pi-mcp-adapter",
            "pi-web-access",
            "pi-subagents",
            "@plannotator/pi-extension",
            "@juicesharp/rpiv-todo",
            "@juicesharp/rpiv-ask-user-question",
            "@juicesharp/rpiv-voice",
            "@juicesharp/rpiv-i18n",
        ],
        "skills": ["~/.pi/agent/skills", ".pi/skills"],
        "extensions": [".pi/extensions"],
        "subagents": {"defaultModel": default, "agentOverrides": {}},
    }
    agents = {}
    for role, spec in roles.items():
        if not isinstance(spec, dict):
            continue
        model = resolve(spec.get("model", default))
        settings["subagents"]["agentOverrides"][role] = model
        tools = (
            []
            if args.no_mcp
            else [f"mcp:{x}" for x in spec.get("mcps", []) if not x.startswith("!")]
        )
        agents[role] = {
            "name": ROLE_MAP.get(role, role),
            "model": model,
            "thinking": spec.get("variant", "medium"),
            "skills": spec.get("skills", []),
            "tools": tools,
        }
    providers = {}
    local_base = args.ollama_base_url or get_ollama_local_base_url()
    local_ids = sorted({v.split("/", 1)[-1] for v in local.values()})
    providers["ollama"] = {
        "baseUrl": local_base,
        "api": "openai-completions",
        "apiKey": "ollama",
        "models": [model_entry(x, local=True) for x in local_ids],
    }
    cloud_path = ROOT / "configs/opencode/ollama-cloud-models.json"
    with cloud_path.open(encoding="utf-8") as f:
        cloud = json.load(f).get("models", {})
    cloud_models = [model_entry(k) for k in cloud]
    running, proxy = check_ollama_daemon()
    if proxy:
        providers["ollama"]["models"] += [model_entry(k + ":cloud") for k in cloud]
    else:
        providers["ollama-cloud"] = {
            "baseUrl": BASE_URLS["ollama-cloud"],
            "api": "openai-completions",
            "apiKey": "$OLLAMA_API_KEY",
            "models": cloud_models,
        }
    providers["meridian"] = {
        "baseUrl": get_meridian_base_url(),
        "api": "openai-responses",
        "apiKey": "$MERIDIAN_API_KEY",
        "models": [],
    }
    providers["openai"] = {
        "baseUrl": BASE_URLS["openai"],
        "api": "openai-completions",
        "apiKey": "$OPENAI_API_KEY",
        "models": [],
    }
    auth = {
        "anthropic": {"type": "api_key", "key": "$ANTHROPIC_API_KEY"},
        "openai": {"type": "api_key", "key": "$OPENAI_API_KEY"},
        "google": {"type": "api_key", "key": "$GOOGLE_API_KEY"},
    }
    # Derive enabledModels from actual provider model IDs instead of hardcoding
    # patterns that may not match any available model (e.g. gpt-4o, qwen3-coder
    # are meaningless in a local-solo tier with only ollama models).
    all_model_ids = [
        m["id"] for prov in providers.values() for m in prov.get("models", [])
    ]
    # Build glob patterns from model family prefixes.
    # - Cloud models (name:tag:cloud) → glob on the name prefix (e.g. "glm-*")
    # - Local Ollama models (name:tag) → include the full ID (tags aren't globbable)
    # - API models (family-variant) → glob on the family prefix (e.g. "claude-*")
    prefixes: set[str] = set()
    for mid in all_model_ids:
        if ":cloud" in mid:
            # Strip ":cloud" suffix, then glob on the family prefix
            base = mid.replace(":cloud", "")
            parts = base.split("-", 1)
            if len(parts) == 2:
                prefixes.add(f"{parts[0]}-*")
            else:
                prefixes.add(base)
        elif ":" in mid:
            # Local Ollama model — include the full ID
            prefixes.add(mid)
        else:
            parts = mid.split("-", 1)
            if len(parts) == 2:
                prefixes.add(f"{parts[0]}-*")
    if default_model:
        prefixes.add(default_model)
    settings["enabledModels"] = sorted(prefixes)
    out = (
        Path(os.environ.get("PI_CODING_AGENT_DIR", "~/.pi/agent")).expanduser()
        if args.mode == "global"
        else Path(".pi/agent")
    )
    files = {
        out / "settings.json": settings,
        out / "models.json": {"providers": providers},
        out / "auth.json": auth,
    }
    files.update(
        {
            out
            / "agents"
            / f"{role}.md": f"---\nname: {a['name']}\nmodel: {a['model']}\nthinking: {a['thinking']}\n---\n\nPi subagent role: {role}.\n"
            for role, a in agents.items()
        }
    )
    for path, content in files.items():
        text = (
            content
            if isinstance(content, str)
            else json.dumps(content, indent=2) + "\n"
        )
        if args.dry_run:
            logger.info("Would write %s", path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not args.no_backup:
            backup_file(str(path), enabled=True)
        write_text_file(str(path), text, backup=False)
    seed_plugin_configs(dry_run=args.dry_run, mode=args.mode)
    logger.info("Pi configured: %s (preset=%s)", out, preset)


if __name__ == "__main__":
    main()
