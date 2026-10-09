#!/usr/bin/env python3
"""Generate Pi agent configuration from the shared OpenCode tier registry."""

import argparse, copy, json, os, shlex, shutil, subprocess, sys
from pathlib import Path
import stat

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(SCRIPT_DIR, "lib"))
import logger  # noqa: E402 -- local imports follow the scripts/lib sys.path bootstrap.
from env import load_env  # noqa: E402 -- local import follows bootstrap.
from cli_helpers import (  # noqa: E402 -- local import follows bootstrap.
    add_common_args,
    add_model_override_args,
    add_min_reasoning_embedding_arg,
)
from constants import (  # noqa: E402 -- local import follows bootstrap.
    BASE_URLS,
    get_litellm_proxy_mode,
    get_meridian_base_url,
    get_ollama_local_base_url,
)
from discover_models import (  # noqa: E402 -- local import follows bootstrap.
    list_cloud_ollama_models,
    list_local_ollama_models,
)
from file_utils import (  # noqa: E402 -- local import follows bootstrap.
    backup_file,
    write_text_file,
)
from opencode_config import (  # noqa: E402 -- local import follows bootstrap.
    get_available_tiers,
)
from provider_endpoints import (  # noqa: E402 -- local import follows bootstrap.
    PROVIDER_ENDPOINTS,
    provider_models,
)
from tier_resolve import (  # noqa: E402 -- local import follows bootstrap.
    get_model_details,
)
from local_engines import (  # noqa: E402 -- local import follows bootstrap.
    engine_gate_active,
    local_endpoint_for,
    resolve_engine,
)
from litellm_aliases import resolve_alias  # noqa: E402
import tier_registry  # noqa: E402 -- local import follows bootstrap.
from model_catalogues import (  # noqa: E402 -- local import follows bootstrap.
    get_catalogue,
)

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
# Cache of model_id -> Ollama model details discovered via `ollama show`.
# A model's native window can be smaller than the global OLLAMA_CONTEXT_LENGTH cap
# (e.g. a 128K model under a 192K cap), so we cap the advertised window at the
# model's own limit to avoid asking it for tokens it can never hold.
_model_details_cache = {}


def _ollama_context_cap():
    """Return the OLLAMA_CONTEXT_LENGTH cap (int), or 128000 if unset/invalid."""
    env_ctx = os.environ.get("OLLAMA_CONTEXT_LENGTH", "")
    return int(env_ctx) if env_ctx.isdigit() else 128000


def _native_context(model_id, provider="ollama"):
    """Best-effort native context window for a model via `ollama show`.

    Returns the model's own context length, or None when unknown/unavailable.
    Cached per model so the `ollama show` subprocess runs at most once each.
    """
    return _model_details(model_id, provider).get("context_length")


def _model_details(model_id, provider="ollama"):
    """Return cached local-model details for a provider/model pair."""
    cache_key = (provider, model_id)
    if cache_key not in _model_details_cache:
        engine = resolve_engine(provider)
        if engine and engine.get("details_provider_arg"):
            _model_details_cache[cache_key] = get_model_details(model_id, provider)
        else:
            _model_details_cache[cache_key] = get_model_details(model_id)
    return _model_details_cache[cache_key]


def _model_context_window(model_id, local, provider="ollama"):
    """Context window to advertise for a model.

    Local Ollama models are capped at min(OLLAMA_CONTEXT_LENGTH, native) so pi
    never requests a window the model can't hold. Ollama cloud models use their
    native window when available; other cloud/API models use the cap.
    """
    cap = _ollama_context_cap()
    if local:
        native = _native_context(model_id, provider)
        engine = resolve_engine(provider)
        if engine and engine.get("context_fallback") is not None:
            return native or engine["context_fallback"]
        if native:
            return min(cap, native)
    elif model_id.endswith(":cloud"):
        native = _native_context(model_id)
        if native:
            return native
    return cap


def _compaction_tokens(local_refs):
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
    natives = []
    for ref in local_refs:
        if "/" in ref:
            provider, model = ref.split("/", 1)
        else:
            provider, model = "ollama", ref
        native = _native_context(model, provider)
        if native:
            natives.append(native)
    base = min(natives + [cap])  # tightest window; never above the OLLAMA cap
    return max(8192, round(0.33 * base))


def model_entry(model_id, name=None, local=False, provider="ollama"):
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
            in _model_details(model_id, provider).get("capabilities", []),
        }
        if compat["supportsReasoningEffort"]:
            compat["thinkingFormat"] = "openai"

    return {
        "id": model_id,
        "name": name or model_id,
        "reasoning": True,
        "input": ["text"],
        "contextWindow": _model_context_window(model_id, local, provider),
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


def build_local_provider(provider, model_ids):
    """Build a gated local-engine Pi provider, or return None when unavailable."""
    if not engine_gate_active(provider):
        return None
    engine = resolve_engine(provider)
    endpoint = local_endpoint_for(provider, "openai")
    if engine is None or endpoint is None:
        return None
    health_check = engine.get("health_check")
    if health_check and not health_check()[0]:
        return None
    base_url, api_key_env = endpoint
    provider_config = {
        "baseUrl": base_url,
        "api": "openai-completions",
        "models": [
            model_entry(model_id, local=True, provider=provider)
            for model_id in model_ids
        ],
    }
    if api_key_env and os.environ.get(api_key_env, "").strip():
        provider_config["apiKey"] = f"${api_key_env}"
    elif not api_key_env or engine.get("api_key_optional"):
        # Keyless-capable engine (e.g. oMLX no-key loopback mode accepts any
        # bearer token): Pi still requires an apiKey field to consider the
        # provider authenticated, so emit a literal placeholder (provider id).
        provider_config["apiKey"] = provider
    # else: the engine requires its API key and it is unset — omit apiKey so
    # pi treats the provider as unauthenticated and skips it in resolution.
    return provider_config


def apply_litellm_provider_overrides(providers, port="4000"):
    if not get_litellm_proxy_mode():
        return
    if os.environ.get("DOTFILES_RUN_LITELLM_SETUP", "0") != "1":
        raise RuntimeError(
            "DOTFILES_USE_LITELLM_PROXY=1 requires DOTFILES_RUN_LITELLM_SETUP=1"
        )
    key_file = Path("~/.local/share/litellm/clients/pi.key").expanduser()
    if (
        any(
            path.is_symlink()
            for path in (key_file.parent.parent, key_file.parent, key_file)
        )
        or not key_file.is_file()
    ):
        raise RuntimeError(
            "Pi LiteLLM key file unavailable; refusing to generate canary config"
        )
    metadata = key_file.stat()
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise RuntimeError(
            "Pi LiteLLM key file permissions are unsafe; refusing to generate canary config"
        )
    client_dir = key_file.parent
    directory_metadata = client_dir.stat()
    if (
        directory_metadata.st_uid != os.getuid()
        or stat.S_IMODE(directory_metadata.st_mode) & 0o077
    ):
        raise RuntimeError(
            "Pi LiteLLM client directory permissions are unsafe; refusing to generate canary config"
        )
    endpoint = f"http://127.0.0.1:{port}/v1"
    for provider in ("openai", "ollama", "omlx"):
        if provider in providers:
            providers[provider]["baseUrl"] = endpoint
            providers[provider]["apiKey"] = f"!cat {shlex.quote(str(key_file))}"


def litellm_cloud_aliases(port="4000"):
    """Return checked-in cloud aliases confirmed by LiteLLM, or empty on UNKNOWN."""
    key_file = Path("~/.local/share/litellm/clients/pi.key").expanduser()
    try:
        if any(
            path.is_symlink()
            for path in (key_file.parent.parent, key_file.parent, key_file)
        ):
            raise ValueError("symlinked key path")
        metadata = key_file.stat()
        directory_metadata = key_file.parent.stat()
        if not key_file.is_file():
            raise ValueError("missing key file")
        if metadata.st_uid != os.getuid():
            raise ValueError("key file has the wrong owner")
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise ValueError("key file has unsafe permissions")
        if directory_metadata.st_uid != os.getuid():
            raise ValueError("client directory has the wrong owner")
        if stat.S_IMODE(directory_metadata.st_mode) & 0o077:
            raise ValueError("unsafe key file")
        key = key_file.read_text(encoding="utf-8").strip()
        if not key:
            raise ValueError("empty key")
        catalogue = get_catalogue(f"http://127.0.0.1:{port}/v1/models", key)
        data = catalogue.get("data") if isinstance(catalogue, dict) else None
        if not isinstance(data, list):
            raise ValueError("malformed catalogue")
        available = set()
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise ValueError("malformed catalogue")
            available.add(item["id"])
    except Exception:
        logger.warning(
            "Pi LiteLLM catalogue unavailable; model availability is UNKNOWN"
        )
        # Explicit empty presence per provider (UNKNOWN discipline): the
        # providers exist, nothing is confirmed — never a silent absence.
        return {provider: {} for provider in ("google", "openrouter", "opencode")}
    routes = {provider: {} for provider in ("google", "openrouter", "opencode")}
    available.update(
        {
            f"litellm/{identity}"
            for identity in available
            if not identity.startswith("litellm/")
        }
    )
    for catalogue_id in available:
        wire = (
            catalogue_id
            if catalogue_id.startswith("litellm/")
            else f"litellm/{catalogue_id}"
        )
        identity = catalogue_id.removeprefix("litellm/")
        if identity.startswith("google/models/"):
            routes["google"][identity.removeprefix("google/models/")] = wire
        elif identity.startswith("openrouter/"):
            routes["openrouter"][identity.removeprefix("openrouter/")] = wire
        elif identity.startswith("opencode/"):
            routes["opencode"][identity.removeprefix("opencode/")] = wire
    routes["_available"] = available
    return routes


def local_chat_model_ids(models, provider):
    """Return chat-capable IDs for a registered local provider."""
    engine = resolve_engine(provider)
    chat_types = engine.get("chat_model_types") if engine else None
    return sorted(
        {
            model["name"]
            for model in models
            if isinstance(model, dict)
            and model.get("provider") == provider
            and (not chat_types or model.get("model_type") in chat_types)
        }
    )


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


def _role_models_from_env():
    """Bridge DOTFILES_ROLE_MODELS into role overrides (flag form wins).

    Mirrors configure-opencode-tier.py: the deprecated
    DOTFILES_LOCAL_FALLBACK_ROLES alias warns and is honoured only when
    the canonical env var is unset.
    """
    values = [
        value.strip()
        for value in os.environ.get("DOTFILES_ROLE_MODELS", "").split(",")
        if value.strip()
    ]
    if values:
        return values
    if os.environ.get("DOTFILES_LOCAL_FALLBACK_ROLES"):
        logger.warning(
            "Deprecated DOTFILES_LOCAL_FALLBACK_ROLES; use DOTFILES_ROLE_MODELS instead"
        )
        return [
            value.strip()
            for value in os.environ.get("DOTFILES_LOCAL_FALLBACK_ROLES", "").split(",")
            if value.strip()
        ]
    return None


def apply_preserved_preferences(settings, prev):
    """Preserve user Pi preferences while codified defaults seed fresh installs."""
    for key in ("tuiMode", "followUpMode", "steeringMode", "showHardwareCursor"):
        if prev.get(key) is not None:
            settings[key] = prev[key]
    for key in ("markdown", "terminal"):
        if isinstance(prev.get(key), dict):
            settings[key] = {**settings.get(key, {}), **prev[key]}
    if "lastChangelogVersion" in prev:
        settings["lastChangelogVersion"] = prev["lastChangelogVersion"]
    else:
        settings.pop("lastChangelogVersion", None)


def main():
    p = argparse.ArgumentParser(
        description="Configure Pi from the shared AI tier registry", allow_abbrev=False
    )
    add_common_args(p, no_backup=True)
    add_model_override_args(p)
    add_min_reasoning_embedding_arg(p)
    p.add_argument("--mode", choices=["global", "project"], default="global")
    available_tiers = get_available_tiers()
    p.add_argument("--preset", choices=available_tiers, required=True)
    p.add_argument(
        "--skip",
        default=None,
        help="Comma-separated steps to skip (currently only: mcps)",
    )
    p.add_argument("--no-local-fallbacks", action="store_true")
    p.add_argument("--ollama-base-url")
    args = p.parse_args()

    # Load ~/.env so standalone runs see gate/endpoint vars (OMLX_* etc.);
    # inside make deploy the parent environment already carries them.
    load_env()

    # Env bridge mirroring configure-opencode-tier.py: DOTFILES_ROLE_MODELS
    # feeds --role-model so role overrides work without flags.
    if args.role_models is None:
        args.role_models = _role_models_from_env()
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
    tier_registry.apply_placeholder_overrides(category_models, args.category_models)
    role_models = tier_registry.materialize_role_models(
        registry,
        resolution_preset if local_preset else preset,
        category_models,
        args.role_models,
    )
    if any(
        isinstance(model, str) and model.startswith("github-copilot/")
        for model in role_models.values()
    ):
        native_auth = (
            Path(os.environ.get("PI_CODING_AGENT_DIR", "~/.pi/agent")).expanduser()
            / "auth.json"
        )
        copilot_auth_detected = False
        try:
            auth_data = json.loads(native_auth.read_text(encoding="utf-8"))
            copilot_auth_detected = any(
                key in auth_data for key in ("copilot", "github-copilot")
            )
        except (OSError, json.JSONDecodeError):
            pass
        if not copilot_auth_detected:
            logger.warning(
                "Preset references github-copilot models — complete Pi's native Copilot /login"
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
    if args.role_models:
        overrides = {}
        for item in args.role_models:
            if "=" in item:
                role, model = item.split("=", 1)
                overrides[role] = model
        applied = {
            role: model
            for role, model in overrides.items()
            if role_models.get(role) == model
        }
        if applied:
            logger.info(
                "Applied role-model overrides: %s",
                json.dumps(applied, sort_keys=True),
            )

    skipped = {item.strip() for item in (args.skip or "").split(",") if item.strip()}
    unknown_skip = skipped - {"mcps"}
    if unknown_skip:
        p.error(f"Unknown --skip step(s): {', '.join(sorted(unknown_skip))}")
    if "mcps" in skipped:
        logger.info("MCPs not configured by configure-pi.py — --skip mcps is a no-op")

    # Local model IDs (the part after "ollama/") + compaction reserve.
    # Computed early so settings["compaction"] and the provider model
    # entries share one set of `ollama show` lookups (cached in _native_ctx_cache).
    local_refs = sorted(
        {v for v in category_models.values() if not v.endswith("no-model-available")}
    )
    ollama_ids = sorted(
        {
            ref.split("/", 1)[-1]
            for ref in local_refs
            if not (
                (
                    engine := resolve_engine(
                        ref.split("/", 1)[0] if "/" in ref else "ollama"
                    )
                )
                and engine.get("provider_config")
            )
        }
    )
    local_engine_ids = {
        provider: local_chat_model_ids(local_models, provider)
        for provider in {
            model.get("provider")
            for model in local_models
            if isinstance(model, dict) and model.get("provider")
        }
        if (engine := resolve_engine(provider)) and engine.get("provider_config")
    }
    cloud_path = ROOT / "configs/opencode/ollama-cloud-models.json"
    with cloud_path.open(encoding="utf-8") as f:
        cloud = json.load(f).get("models", {})
    pi_litellm_proxy = get_litellm_proxy_mode()
    proxy_key_path = Path("~/.local/share/litellm/clients/pi.key").expanduser()
    gateway_models = set()
    cloud_routes = {}
    if pi_litellm_proxy:
        if os.environ.get("DOTFILES_RUN_LITELLM_SETUP", "0") != "1":
            raise RuntimeError(
                "DOTFILES_USE_LITELLM_PROXY=1 requires DOTFILES_RUN_LITELLM_SETUP=1"
            )
        if (
            any(
                path.is_symlink()
                for path in (
                    proxy_key_path.parent.parent,
                    proxy_key_path.parent,
                    proxy_key_path,
                )
            )
            or not proxy_key_path.is_file()
        ):
            raise RuntimeError(f"Pi LiteLLM key file is unavailable: {proxy_key_path}")
        key_metadata = proxy_key_path.stat()
        client_metadata = proxy_key_path.parent.stat()
        if (
            key_metadata.st_uid != os.getuid()
            or stat.S_IMODE(key_metadata.st_mode) != 0o600
            or client_metadata.st_uid != os.getuid()
            or stat.S_IMODE(client_metadata.st_mode) & 0o077
        ):
            raise RuntimeError(
                f"Pi LiteLLM client key permissions are unsafe: {proxy_key_path}"
            )
        cloud_routes = litellm_cloud_aliases(os.environ.get("LITELLM_PORT", "4000"))
        # Every advertised gateway identity belongs in Pi's model list —
        # users pick from /model, not just preset role selections.
        for advertised in cloud_routes.get("_available", set()):
            if advertised.startswith("litellm/"):
                gateway_models.add(advertised.removeprefix("litellm/"))
        resolved = {}
        for role, model_ref in role_models.items():
            if (
                not isinstance(model_ref, str)
                or "/" not in model_ref
                or model_ref.startswith("_local:")
            ):
                resolved[role] = model_ref
                continue
            provider, model_id = model_ref.split("/", 1)
            if provider == "anthropic":
                lookup_id = model_id
                transport = resolve_alias(
                    f"meridian/{model_id}",
                    True,
                    client_key=proxy_key_path.resolve(),
                    gateway_url=f"http://127.0.0.1:{os.environ.get('LITELLM_PORT', '4000')}/v1",
                )["model"]
                canonical_ref = f"meridian/{model_id}"
                if transport not in cloud_routes.get("_available", set()):
                    logger.warning(
                        "Pi LiteLLM alias is not advertised for %s", canonical_ref
                    )
                    resolved[role] = None
                    continue
            elif provider in cloud_routes:
                lookup_id = (
                    model_id.removeprefix("models/")
                    if provider == "google"
                    else model_id
                )
                transport = cloud_routes.get(provider, {}).get(lookup_id)
                if transport is None:
                    logger.warning(
                        "Pi LiteLLM alias is not advertised for %s", model_ref
                    )
                    resolved[role] = None
                    continue
                canonical_ref = (
                    f"google/models/{lookup_id}" if provider == "google" else model_ref
                )
            else:
                canonical_ref = model_ref
                transport = resolve_alias(
                    canonical_ref,
                    True,
                    client_key=proxy_key_path.resolve(),
                    gateway_url=f"http://127.0.0.1:{os.environ.get('LITELLM_PORT', '4000')}/v1",
                )["model"]
                if transport not in cloud_routes.get("_available", set()):
                    logger.warning(
                        "Pi LiteLLM alias is not advertised for %s", model_ref
                    )
                    resolved[role] = None
                    continue
            gateway_models.add(canonical_ref)
            resolved[role] = transport
        role_models = resolved
    compaction_tokens = _compaction_tokens(local_refs)

    orchestrator_model = role_models["orchestrator"]
    if orchestrator_model is None:
        orchestrator_model = next(
            iter(category_models.values()),
            next(
                (
                    model
                    for role, model in role_models.items()
                    if role != "orchestrator" and isinstance(model, str) and model
                ),
                None,
            ),
        )
    if orchestrator_model is None:
        logger.warning(
            "No default model could be derived from the preset or local models"
        )
        default = "ollama/no-model-available"
    else:
        default = orchestrator_model
    if pi_litellm_proxy and isinstance(default, str) and "/" in default:
        canonical_default = default.removeprefix("litellm/")
        if canonical_default == "ollama/no-model-available":
            raise RuntimeError("Pi default model has no advertised LiteLLM alias")
        if canonical_default.startswith("google/"):
            key = canonical_default.split("/", 2)[-1].removeprefix("models/")
            default = cloud_routes.get("google", {}).get(key)
        elif canonical_default.startswith("openrouter/"):
            key = canonical_default.split("/", 1)[1]
            default = cloud_routes.get("openrouter", {}).get(key)
        else:
            default = resolve_alias(
                canonical_default,
                True,
                client_key=proxy_key_path.resolve(),
                gateway_url=f"http://127.0.0.1:{os.environ.get('LITELLM_PORT', '4000')}/v1",
            )["model"]
            if default not in cloud_routes.get("_available", set()):
                raise RuntimeError("Pi default model has no advertised LiteLLM alias")
            gateway_models.add(canonical_default)
        if default is None or default == "ollama/no-model-available":
            raise RuntimeError("Pi default model has no advertised LiteLLM alias")
    provider, _, default_model = default.partition("/")
    settings = {
        "defaultProvider": provider,
        "defaultModel": default_model or default,
        "defaultThinkingLevel": roles.get("orchestrator", {}).get("variant", "medium"),
        "theme": "light/dark",
        "tuiMode": "fullscreen",
        "markdown": {"mermaid": "final"},
        "followUpMode": "all",
        "steeringMode": "all",
        "terminal": {"showTerminalProgress": True},
        "showHardwareCursor": True,
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
            "npm:pi-edit-session-in-place",
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
        # Unresolvable role models (e.g. _local placeholders on a machine with
        # no local daemon) must not become agent overrides: a None model would
        # crash provider parsing later and pin the built-in to nothing.
        if not role_models[role]:
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
        "models": [model_entry(x, local=True) for x in ollama_ids],
    }
    for provider, model_ids in local_engine_ids.items():
        provider_config = build_local_provider(provider, model_ids)
        if provider_config:
            providers[provider] = provider_config
    if cloud and not pi_litellm_proxy:
        providers["ollama-cloud"] = {
            "baseUrl": BASE_URLS["ollama-cloud"],
            "api": "openai-completions",
            "apiKey": "$OLLAMA_API_KEY",
            "models": [model_entry(model_id) for model_id in cloud],
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
    skipped_providers = []
    skipped_provider_names = []
    emitted_providers = []
    for provider, endpoint in PROVIDER_ENDPOINTS.items():
        if pi_litellm_proxy and provider in ("google", "openrouter"):
            continue
        # Registry entries without an "api"/"allowlist" pair (litellm-only
        # catalogue providers like cerebras/cohere/huggingface) have no pi
        # client wiring — skip them instead of KeyError-ing.
        if "api" not in endpoint or "allowlist" not in endpoint:
            continue
        key_env = endpoint["apiKeyEnv"]
        if not os.environ.get(key_env, "").strip():
            skipped_providers.append(f"{provider} ({key_env})")
            skipped_provider_names.append(provider)
            continue
        providers[provider] = {
            "baseUrl": endpoint["baseUrl"],
            "api": endpoint["api"],
            "apiKey": f"${key_env}",
            "models": [model_entry(model_id) for model_id in provider_models(provider)],
        }
        emitted_providers.append(provider)
    if emitted_providers:
        logger.info(
            "Providers emitted with configured API keys: %s",
            ", ".join(emitted_providers),
        )
    if skipped_providers:
        logger.warning(
            "Providers skipped because API keys are unavailable: %s",
            ", ".join(skipped_providers),
        )
    if pi_litellm_proxy:
        providers.clear()
        if not gateway_models:
            raise RuntimeError(
                "Pi LiteLLM mode has no resolvable canonical model aliases"
            )
        entries = []
        for identity in sorted(gateway_models):
            entry = model_entry(identity, provider="litellm")
            if identity.startswith("google/models/"):
                entry["compat"] = {"supportsStore": False}
            entries.append(entry)
        key_indirection = resolve_alias(
            next(iter(sorted(gateway_models)), "ollama/no-model-available"),
            True,
            client_key=f"!cat {shlex.quote(str(proxy_key_path.resolve()))}",
            gateway_url=f"http://127.0.0.1:{os.environ.get('LITELLM_PORT', '4000')}/v1",
        )
        providers["litellm"] = {
            "baseUrl": f"http://127.0.0.1:{os.environ.get('LITELLM_PORT', '4000')}/v1",
            "api": "openai-completions",
            "apiKey": key_indirection["api_key"],
            "models": entries,
        }
    skipped_role_overrides = {}
    builtin_roles = {builtin: role for role, builtin in ROLE_TO_BUILTIN.items()}
    for builtin, override in list(settings["subagents"]["agentOverrides"].items()):
        model_ref = override.get("model") or ""
        provider = model_ref.split("/", 1)[0] if "/" in model_ref else ""
        if provider in skipped_provider_names:
            skipped_role_overrides.setdefault(provider, []).append(
                builtin_roles.get(builtin, builtin)
            )
            del settings["subagents"]["agentOverrides"][builtin]
    if skipped_role_overrides:
        logger.warning(
            "Omitted Pi agent overrides for unavailable providers: %s",
            "; ".join(
                f"{provider} ({', '.join(sorted(roles))})"
                for provider, roles in sorted(skipped_role_overrides.items())
            ),
        )
    default_provider = default.split("/", 1)[0] if "/" in default else ""
    if default_provider in skipped_provider_names:
        available_model = next(
            (
                f"{provider}/{entry['id']}"
                for provider, provider_config in providers.items()
                for entry in provider_config.get("models", [])
                if entry.get("id")
            ),
            "",
        )
        default = available_model or "ollama/no-model-available"
        settings["defaultProvider"], _, settings["defaultModel"] = default.partition(
            "/"
        )
        settings["subagents"]["defaultModel"] = default
    auth = {
        "anthropic": {"type": "api_key", "key": "$ANTHROPIC_API_KEY"},
        "openai": {"type": "api_key", "key": "$OPENAI_API_KEY"},
    }
    for provider, endpoint in PROVIDER_ENDPOINTS.items():
        if not (pi_litellm_proxy and provider in ("google", "openrouter")):
            auth[provider] = {"type": "api_key", "key": f"${endpoint['apiKeyEnv']}"}
    if pi_litellm_proxy:
        # Pi prefers the static auth entry in auth.json over a provider's
        # apiKey from models.json; when LITELLM_PI_KEY is not exported to the
        # Pi process the litellm entry would stay unresolved while Pi still
        # sends the stale stored credential. models.json already supplies the
        # gateway key for the litellm provider, so emit no conflicting auth
        # entry at all.
        auth = {}
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
        if pi_litellm_proxy:
            prefixes.add(f"litellm/{mid}")
            continue
        if mid.startswith(("google/models/", "openrouter/")):
            prefixes.add(f"litellm/{mid}")
            continue
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
    previous = {}
    managed = set(ROLE_TO_BUILTIN.values()) | set(role_models)
    if prev_settings.exists():
        try:
            previous = json.loads(prev_settings.read_text(encoding="utf-8"))
            old_overrides = (
                previous.get("subagents", {}).get("agentOverrides", {}) or {}
            )
            for name, val in old_overrides.items():
                if name not in managed:
                    settings["subagents"]["agentOverrides"][name] = val
            apply_preserved_preferences(settings, previous)
        except Exception:  # pylint: disable=broad-exception-caught
            pass
    settings["theme"] = os.environ.get("PI_THEME", "").strip() or previous.get(
        "theme", "light/dark"
    )

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
