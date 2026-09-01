#!/usr/bin/env python3
"""Generate Pi agent configuration from the shared OpenCode tier registry."""

import argparse, copy, json, os, shutil, subprocess, sys
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(SCRIPT_DIR, "lib"))
import logger
from cli_helpers import (
    add_common_args,
    add_local_fallback_args,
    add_min_reasoning_embedding_arg,
)
from constants import (
    BASE_URLS,
    check_ollama_daemon,
    get_meridian_base_url,
    get_ollama_local_base_url,
)
from discover_models import list_local_ollama_models
from file_utils import backup_file, write_text_file
from opencode_config import get_available_tiers
from tier_resolve import get_model_details
import tier_registry

# pi-skills is not an npm package; skills are provisioned through settings["skills"].
# Keep this mechanism for future packages shipped with pi-core.
_BUILTIN_PACKAGES = frozenset()

ROOT = Path(SCRIPT_DIR).parent
SLIM = ROOT / "configs/opencode/oh-my-opencode-slim.json"
# Map OMO-Slim preset roles to the matching pi-subagents built-in. The six
# built-ins (scout, researcher, worker, reviewer, oracle, delegate) already
# carry the prompt, tools, and thinking; we only pin their model via
# agentOverrides. Roles with no built-in (designer, council) are intentionally
# absent so they fall through to subagents.defaultModel.
ROLE_TO_BUILTIN = {
    "orchestrator": "delegate",
    "oracle": "oracle",
    "librarian": "researcher",
    "explorer": "scout",
    "fixer": "worker",
    "observer": "reviewer",
}
OPENCODE_ONLY_PRESETS = frozenset({"openai", "thirtydollars", "opencode-zen-free"})


def get_pi_available_tiers():
    """Return registry tiers supported by Pi's available providers."""
    return [tier for tier in get_available_tiers() if tier not in OPENCODE_ONLY_PRESETS]


# Cache of model_id -> Ollama model details discovered via `ollama show`.
# A model's native window can be smaller than the global OLLAMA_CONTEXT_LENGTH cap
# (e.g. a 128K model under a 192K cap), so we cap the advertised window at the
# model's own limit to avoid asking it for tokens it can never hold.
_model_details_cache = {}


def _ollama_context_cap():
    """Return the OLLAMA_CONTEXT_LENGTH cap (int), or 128000 if unset/invalid."""
    env_ctx = os.environ.get("OLLAMA_CONTEXT_LENGTH", "")
    return int(env_ctx) if env_ctx.isdigit() else 128000


def _native_context(model_id):
    """Best-effort native context window for a model via `ollama show`.

    Returns the model's own context length, or None when unknown/unavailable.
    Cached per model so the `ollama show` subprocess runs at most once each.
    """
    return _model_details(model_id).get("context_length")


def _model_details(model_id):
    """Return cached Ollama details for a model."""
    if model_id not in _model_details_cache:
        _model_details_cache[model_id] = get_model_details(model_id)
    return _model_details_cache[model_id]


def _model_context_window(model_id, local):
    """Context window to advertise for a model.

    Local Ollama models are capped at min(OLLAMA_CONTEXT_LENGTH, native) so pi
    never requests a window the model can't hold. Ollama cloud models use their
    native window when available; other cloud/API models use the cap.
    """
    cap = _ollama_context_cap()
    if local:
        native = _native_context(model_id)
        if native:
            return min(cap, native)
    elif model_id.endswith(":cloud"):
        native = _native_context(model_id)
        if native:
            return native
    return cap


def _compaction_tokens(local_ids):
    """Compaction reserve/keep as 33% of the tightest usable local context.

    Pi's compaction is a single global block, so one reserve value yields
    different per-model *trigger fractions* (auto-compaction fires when
    contextTokens > contextWindow - reserveTokens). Basing 33% on the smallest
    native window (capped at OLLAMA_CONTEXT_LENGTH) guarantees the tightest model
    triggers at ~67% (the DCP strong threshold) while larger windows get gentler,
    later triggers -- the safe direction. Falls back to the OLLAMA cap when no
    local model exposes a native context.
    """
    cap = _ollama_context_cap()
    natives = [n for n in (_native_context(m) for m in local_ids) if n]
    base = min(natives + [cap])  # tightest window; never above the OLLAMA cap
    return max(8192, round(0.33 * base))


def model_entry(model_id, name=None, local=False):
    """Build a pi model entry.

    Context window is the OLLAMA_CONTEXT_LENGTH cap, further capped at the model's
    own native window for local Ollama models (see _model_context_window). This
    keeps pi in sync with the Ollama daemon's KV sizing without ever asking a
    model for a window it can't hold.
    """
    compat: dict[str, object] = {}
    if local:
        compat = {
            "supportsDeveloperRole": False,
            "supportsReasoningEffort": "thinking"
            in _model_details(model_id).get("capabilities", []),
        }
        if compat["supportsReasoningEffort"]:
            compat["thinkingFormat"] = "openai"

    return {
        "id": model_id,
        "name": name or model_id,
        "reasoning": True,
        "input": ["text"],
        "contextWindow": _model_context_window(model_id, local),
        "maxTokens": 32000,
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        **(
            {
                "compat": compat,
                "thinkingLevelMap": {"xhigh": "max"},
            }
            if local
            else {}
        ),
    }


def _install_package(pkg, dry_run, mode):
    """Install one pi package via `pi install`, prefixing npm: when needed."""
    if pkg in _BUILTIN_PACKAGES:
        return
    if shutil.which("pi") is None:
        logger.warning("`pi` CLI not found; skipping package install")
        return
    source = pkg if pkg.startswith("npm:") else f"npm:{pkg}"
    if dry_run:
        logger.info("Would install package: %s", source)
        return
    logger.info("Installing package: %s", source)
    result = subprocess.run(
        ["pi", "install", source],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.warning("Failed to install %s: %s", source, result.stderr.strip())
    else:
        logger.info("Installed package: %s", source)


def _ensure_packages(packages, dry_run=False, mode="global"):
    """Ensure all configured packages are installed (idempotent).

    Runs `pi list --no-approve` to discover what is already installed, then
    installs only the missing ones. Builtin packages are skipped (they ship with
    pi-core and have no install step); in dry-run the install is logged, not
    executed.
    """
    if shutil.which("pi") is None:
        logger.warning("`pi` CLI not found; skipping package installation")
        return
    try:
        result = subprocess.run(
            ["pi", "list", "--no-approve"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.warning(
                "Could not run `pi list` (%s); skipping package install",
                result.stderr.strip(),
            )
            return
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Could not run `pi list` (%s); skipping package install", exc)
        return

    installed = set()
    for line in result.stdout.splitlines():
        name = line.strip()
        if (
            not name
            or name.startswith("User packages:")
            or name.startswith("Project packages:")
            or name.startswith("/")
        ):
            continue
        if name.startswith("npm:"):
            name = name[4:]
        installed.add(name)

    to_install = [
        pkg
        for pkg in packages
        if pkg not in _BUILTIN_PACKAGES and pkg.removeprefix("npm:") not in installed
    ]
    if not to_install:
        logger.info("All %d packages already installed", len(packages))
        return
    logger.info(
        "Installing %d missing package(s): %s", len(to_install), ", ".join(to_install)
    )
    for pkg in to_install:
        _install_package(pkg, dry_run, mode)


def _cleanup_generated_agents(out, roles, dry_run):
    """Remove the stub agents/<role>.md (and .bak) files a previous run
    generated. They collide by name with the pi-subagents built-ins, so a
    leftover stub would override the real built-in. Only files that contain
    the generation marker are removed, preserving genuine user agents.
    Idempotent and dry-run aware (in dry-run it logs intent, deletes nothing).
    """
    marker = "Pi subagent role:"
    removed = 0
    for role in roles:
        for suffix in (".md", ".md.bak"):
            path = out / "agents" / f"{role}{suffix}"
            if not path.exists():
                continue
            try:
                is_stub = marker in path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                is_stub = False
            if not is_stub:
                continue
            if dry_run:
                logger.info("Would remove stale generated agent: %s", path)
            else:
                path.unlink()
            removed += 1
    if removed:
        logger.info("Removed %d stale generated subagent file(s)", removed)


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
    add_common_args(p, no_backup=True)
    add_local_fallback_args(p)
    add_min_reasoning_embedding_arg(p)
    p.add_argument("--mode", choices=["global", "project"], default="global")
    available_tiers = get_pi_available_tiers()
    unsupported_tiers = sorted(set(get_available_tiers()) - set(available_tiers))
    if unsupported_tiers:
        logger.warning(
            "Skipping %s: OpenCode-only preset (requires opencode/github-copilot providers)",
            ", ".join(unsupported_tiers),
        )
    p.add_argument("--preset", choices=available_tiers, required=True)
    p.add_argument(
        "--skip",
        default=None,
        help="Comma-separated steps to skip (currently only: mcps)",
    )
    p.add_argument("--no-local-fallbacks", action="store_true")
    p.add_argument("--ollama-base-url")
    args = p.parse_args()
    registry = copy.deepcopy(tier_registry.load_registry(SLIM))
    preset = args.preset or registry.get("preset", "pro-plus")
    roles = tier_registry.get_preset(registry, preset)
    local_preset = tier_registry.uses_local_placeholders(registry, preset)
    if args.local_fallback_preset:
        resolution_preset = args.local_fallback_preset
    elif local_preset:
        resolution_preset = preset
    else:
        resolution_preset = "local"

    if args.no_local_fallbacks and local_preset:
        logger.warning("--no-local-fallbacks is ignored for local preset %s", preset)
    discover_local = not args.no_local_fallbacks or local_preset
    if discover_local:
        local_models = list_local_ollama_models()
    else:
        logger.info(
            "Skipping local Ollama model discovery for cloud preset %s "
            "(--no-local-fallbacks)",
            preset,
        )
        local_models = []
    category_models = (
        tier_registry.classify_models_for_preset(
            local_models,
            registry,
            resolution_preset,
            args.min_reasoning_embedding,
        )
        if local_models
        else {}
    )
    if not category_models:
        if not local_models:
            logger.warning(
                "No local Ollama models found; local model fallbacks are unavailable"
            )
    tier_registry.apply_placeholder_overrides(
        category_models, args.local_fallback_placeholder
    )
    role_models = tier_registry.materialize_role_models(
        registry,
        resolution_preset if local_preset else preset,
        category_models,
        args.local_fallback_role,
    )
    unresolved = {
        value
        for value in role_models.values()
        if isinstance(value, str) and value.startswith("_local:")
    }
    if unresolved:
        logger.warning(
            "Unresolved local model placeholders; using ollama/no-model-available: %s",
            ", ".join(sorted(unresolved)),
        )
        role_models = {
            role: ("ollama/no-model-available" if model in unresolved else model)
            for role, model in role_models.items()
        }
    if args.local_fallback_preset:
        logger.info("Using local fallback preset: %s", args.local_fallback_preset)
    if category_models:
        logger.info(f"Classified local models: {json.dumps(category_models, indent=2)}")

    skipped = {item.strip() for item in (args.skip or "").split(",") if item.strip()}
    unknown_skip = skipped - {"mcps"}
    if unknown_skip:
        p.error(f"Unknown --skip step(s): {', '.join(sorted(unknown_skip))}")
    if "mcps" in skipped:
        logger.info("MCPs not configured by configure-pi.py — --skip mcps is a no-op")

    # Local model IDs (the part after "ollama/") + compaction reserve.
    # Computed early so settings["compaction"] and the provider model
    # entries share one set of `ollama show` lookups (cached in _native_ctx_cache).
    local_ids = sorted(
        {
            v.split("/", 1)[-1]
            for v in category_models.values()
            if not v.endswith("no-model-available")
        }
    )
    compaction_tokens = _compaction_tokens(local_ids)

    orchestrator_model = role_models["orchestrator"]
    if orchestrator_model is None:
        orchestrator_model = next(iter(category_models.values()), None)
    if orchestrator_model is None:
        logger.warning(
            "No default model could be derived from the preset or local models"
        )
        default = "ollama/no-model-available"
    else:
        default = orchestrator_model
    provider, _, default_model = default.partition("/")
    settings = {
        "defaultProvider": provider,
        "defaultModel": default_model or default,
        "defaultThinkingLevel": roles.get("orchestrator", {}).get("variant", "medium"),
        "theme": os.environ.get("PI_THEME", "dark"),
        "compaction": {
            "enabled": True,
            "reserveTokens": compaction_tokens,
            "keepRecentTokens": compaction_tokens,
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
            "npm:pi-mcp-adapter",
            "npm:pi-web-access",
            "npm:pi-subagents",
            "npm:@plannotator/pi-extension",
            "npm:@juicesharp/rpiv-todo",
            "npm:@juicesharp/rpiv-ask-user-question",
            "npm:@juicesharp/rpiv-voice",
            "npm:@juicesharp/rpiv-i18n",
        ],
        "skills": ["~/.pi/agent/skills", ".pi/skills"],
        "extensions": [".pi/extensions"],
        "subagents": {"defaultModel": default, "agentOverrides": {}},
    }
    # Pin each built-in's model via agentOverrides. The built-ins have no
    # `model` in frontmatter, so without this they inherit only
    # subagents.defaultModel. Model-only: each built-in keeps its native
    # thinking level. Roles with no built-in (designer, council) are skipped.
    for role, builtin in ROLE_TO_BUILTIN.items():
        if role not in role_models:
            continue
        override = {"model": role_models[role]}
        role_config = roles.get(role, {})
        if isinstance(role_config, dict) and role_config.get("variant"):
            override["thinking"] = role_config["variant"]
        settings["subagents"]["agentOverrides"][builtin] = override
    providers = {}
    local_base = args.ollama_base_url or get_ollama_local_base_url()
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
    # patterns that may not match any available model in a local-solo tier.
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
            prefixes.add(mid.rsplit("/", 1)[-1])
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
    # Preserve any user-added agentOverrides we do not manage (e.g. a custom
    # agent the user added by hand). The six built-in names are (re)set
    # from the preset above; everything else is carried over so it survives
    # a regenerate. Legacy entries written by pre-ROLE_TO_BUILTIN versions
    # (string-form overrides keyed by preset role name, e.g.
    # "orchestrator": "ollama/old-model") are dropped: role names are now
    # system-owned (pinned via builtins or falling through to
    # subagents.defaultModel), so carrying them over would pin roles to
    # models that may no longer exist locally.
    prev_settings = out / "settings.json"
    managed = set(ROLE_TO_BUILTIN.values()) | set(role_models)
    if prev_settings.exists():
        try:
            old_overrides = (
                json.loads(prev_settings.read_text(encoding="utf-8"))
                .get("subagents", {})
                .get("agentOverrides", {})
                or {}
            )
            for name, val in old_overrides.items():
                if name not in managed:
                    settings["subagents"]["agentOverrides"][name] = val
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    files = {
        out / "settings.json": settings,
        out / "models.json": {"providers": providers},
        out / "auth.json": auth,
    }
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
    # Remove the stub agents/<role>.md (and .bak) files a previous run
    # generated; they shadow the pi-subagents built-ins by name, so a
    # leftover stub would override the real built-in. Only files bearing
    # the generation marker are touched, so a genuine user agent is left alone.
    _cleanup_generated_agents(out, roles, args.dry_run)
    seed_plugin_configs(dry_run=args.dry_run, mode=args.mode)
    # Ensure all referenced packages are installed (idempotent).
    _ensure_packages(settings["packages"], args.dry_run, args.mode)
    logger.info("Pi configured: %s (preset=%s)", out, preset)


if __name__ == "__main__":
    main()
