#!/usr/bin/env python3
"""Generate and synchronize JetBrains model profiles."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import urllib.parse
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger
import tier_registry
from model_catalogues import open_same_origin
from ai_models import resolve_model
from cli_helpers import add_model_override_args, add_min_reasoning_embedding_arg
from constants import (
    MERIDIAN_DEFAULT_HOST,
    MERIDIAN_DEFAULT_PORT,
    get_ollama_local_base_url,
)
from discover_models import list_local_ollama_models
from env import load_env
from local_engines import (
    active_engine_pools,
    engine_gate_active,
    local_endpoint_for,
    resolve_engine,
)

JUNIE_LITELLM_ENV = "DOTFILES_JUNIE_USE_LITELLM"
LITELLM_ROUTED_PROVIDERS = {
    "ollama",
    "omlx",
    "ollama-cloud",
    "openai",
    "google",
    "openrouter",
    "opencode",
}
JUNIE_KEY_FILE = "~/.local/share/litellm/clients/junie.key"
PROFILE_MANIFEST = ".dotfiles-generated-profiles.json"


def read_junie_key() -> str:
    """Read only Junie's private app key file; never source service.env."""
    path = os.path.expanduser(JUNIE_KEY_FILE)
    key_path = Path(path)
    home = Path.home()
    owner = getattr(os, "getuid", lambda: None)()
    if owner is None:
        return ""
    if any(
        component.is_symlink()
        for component in (
            home / ".local",
            home / ".local/share",
            home / ".local/share/litellm",
            key_path.parent,
            key_path,
        )
    ):
        return ""
    try:
        directory_mode = stat.S_IMODE(key_path.parent.stat().st_mode)
        file_stat = key_path.stat()
        if (
            directory_mode != 0o700
            or key_path.parent.stat().st_uid != owner
            or file_stat.st_uid != owner
            or stat.S_IMODE(file_stat.st_mode) != 0o600
            or not stat.S_ISREG(file_stat.st_mode)
        ):
            return ""
        return key_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def valid_litellm_endpoint(value: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "http"
        and parsed.hostname == "127.0.0.1"
        and port is not None
        and 1 <= port <= 65535
        and parsed.path in {"/v1", "/v1/"}
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
    )


def select_model_groups(groups: dict, use_litellm: bool) -> dict:
    """Select direct groups or their LiteLLM equivalents, never both."""
    if not use_litellm:
        return {
            name: group
            for name, group in groups.items()
            if group.get("provider") != "litellm"
        }
    selected = {}
    for name, group in groups.items():
        provider = group.get("provider")
        if provider == "litellm":
            continue
        if provider in LITELLM_ROUTED_PROVIDERS:
            routed_name = f"litellm-{name}"
            if routed_name in groups:
                selected[routed_name] = groups[routed_name]
            continue
        selected[name] = group
    return selected


def litellm_catalogue_models(base_url: str, api_key: str) -> set[str] | None:
    """Read LiteLLM's model catalogue; None means availability is unknown."""
    try:
        parsed = urllib.parse.urlsplit(base_url)
        port = parsed.port
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or port is None
            or not 1 <= port <= 65535
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            return None
        origin = f"http://127.0.0.1:{port}"
        request = urllib.request.Request(
            f"{origin}/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            method="GET",
        )
        with open_same_origin(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            return None
        if any(
            not isinstance(entry, dict) or not isinstance(entry.get("id"), str)
            for entry in data
        ):
            return None
        return {entry["id"] for entry in data}
    except Exception:
        logger.warning(
            "LiteLLM model catalogue unavailable; exact model availability is unknown"
        )
        return None


def filter_litellm_groups(groups: dict, available_models: set[str] | None) -> dict:
    """Omit known-unavailable LiteLLM model groups; preserve on unknown catalogue."""
    if available_models is None:
        return groups
    return {
        name: group
        for name, group in groups.items()
        if group.get("provider") != "litellm"
        or all(
            model in available_models
            for model in (group.get("primaryModel", ""), group.get("fasterModel", ""))
            if model
        )
    }


def normalize_endpoint(base_url: str, api_type: str) -> str:
    """Return the full Junie endpoint for an OpenAI-compatible API type."""
    base_url = base_url.strip().rstrip("/")
    if re.search(r"/v\d+[A-Za-z0-9.-]+(?:/.*)?$", base_url):
        suffix = {
            "OpenAIResponses": "/responses",
            "OpenAICompletion": "/chat/completions",
        }.get(api_type)
        return f"{base_url}{suffix}" if suffix else base_url
    for suffix in ("/v1/responses", "/v1/chat/completions", "/v1"):
        if base_url.endswith(suffix):
            base_url = base_url[: -len(suffix)]
            break
    endpoint = {
        "OpenAIResponses": "/v1/responses",
        "OpenAICompletion": "/v1/chat/completions",
    }.get(api_type)
    return f"{base_url}{endpoint}" if endpoint else base_url


def provider_key_available(key_env: str) -> bool:
    if key_env == "LITELLM_JUNIE_KEY":
        return bool(read_junie_key())
    if os.environ.get(key_env, "").strip():
        return True
    return False


def build_provider_configs(cfg: dict, junie_key: str = "") -> dict:
    provider_configs = {}
    for name, definition in cfg.get("providers", {}).items():
        key_env = definition.get("apiKeyEnv", "")
        if name == "litellm":
            has_key = bool(junie_key)
        else:
            has_key = provider_key_available(key_env) if key_env else False
        if key_env and not has_key:
            logger.warning(
                f"Provider {name}: {key_env} is unset — Junie refuses to load "
                "profiles referencing an undefined variable"
            )
        # LiteLLM is the sole exception: Junie does not inherit service.env,
        # so its narrowly scoped app key is injected into private profiles.
        # Other providers retain Junie's native ${VAR_NAME} expansion.
        api_key = (
            junie_key
            if name == "litellm" and has_key
            else f"${{{key_env}}}" if key_env and has_key else ""
        )
        host_alt = definition.get("hostEnvAlt", "")
        base_url = os.environ.get(host_alt, "").strip().rstrip("/") if host_alt else ""
        engine = resolve_engine(name)
        if not base_url and engine:
            endpoint = local_endpoint_for(name, "openai")
            if endpoint:
                base_url = endpoint[0]
        elif not base_url and (definition.get("hostEnv") or definition.get("portEnv")):
            if name == "ollama":
                base_url = get_ollama_local_base_url()
            elif name == "meridian":
                host = (
                    os.environ.get(definition.get("hostEnv", ""))
                    or MERIDIAN_DEFAULT_HOST
                )
                port = (
                    os.environ.get(definition.get("portEnv", ""))
                    or MERIDIAN_DEFAULT_PORT
                )
                base_url = f"http://{host}:{port}/v1"
        if not base_url:
            base_url = definition.get("baseUrl", "")
        base_env = definition.get("baseUrlEnv", "")
        if base_env and os.environ.get(base_env, "").strip():
            base_url = os.environ[base_env].strip().rstrip("/")
        if name == "litellm" and (not has_key or not valid_litellm_endpoint(base_url)):
            logger.warning(
                "LiteLLM Junie profile unavailable: private key or safe loopback endpoint missing"
            )
            continue
        base_url = normalize_endpoint(base_url, definition.get("apiType", ""))
        provider_config = {
            "baseUrl": base_url,
            "apiType": definition.get("apiType", ""),
            "apiKey": api_key,
        }
        if engine and engine.get("provider_config") and not api_key:
            provider_config.pop("apiKey")
        provider_configs[name] = provider_config
    return provider_configs


def model_provider(model_ref: str) -> str:
    if model_ref.startswith("ollama-cloud/"):
        return "ollama-cloud"
    if model_ref.startswith("openai/"):
        return "openai"
    if model_ref.startswith("anthropic/"):
        return "meridian"
    if model_ref.startswith("ollama/"):
        return "ollama"
    if model_ref.startswith("omlx/"):
        return "omlx"
    return model_ref_provider(model_ref)


def model_ref_provider(model_ref: str) -> str:
    return model_ref.split("/", 1)[0] if "/" in model_ref else ""


def model_id(model_ref: str, provider_hint: str = "") -> str:
    # Tier roles name Anthropic models with their source namespace, but Junie
    # sends requests to Meridian, whose /v1/models catalogue uses bare IDs.
    if provider_hint == "meridian" and model_ref.startswith("anthropic/"):
        return model_ref[len("anthropic/") :]
    if provider_hint and not model_ref.startswith(f"{provider_hint}/"):
        return model_ref
    return model_ref.split("/", 1)[1] if "/" in model_ref else model_ref


def litellm_alias(model_ref: str, provider: str) -> str:
    """Return the exact LiteLLM wire alias for a direct provider model ref."""
    model = model_id(model_ref, provider)
    if provider == "google":
        return f"google/models/{model.removeprefix('models/')}"
    return f"{provider}/{model}"


def lookup_model_temperature(model_name: str, overrides: dict) -> float | None:
    matches = [p for p in overrides if model_name.lower().startswith(p.lower())]
    return overrides[max(matches, key=len)] if matches else None


def parse_local_models(value: str):
    """Parse JSON model entries from the wrapper, retaining bare-name compatibility."""
    value = value.strip()
    if value.startswith("["):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
    return value.split()


def append_pool_profile_specs(specs, pools=None):
    """Append one profile spec per chat-capable pool model (registry-driven).

    Selection parity, N-engine contract: every gate-active engine's
    chat-capable models become selectable Junie profiles named
    ``local-<provider>-<slug>.json``; ``fasterModel`` is the second-ranked
    compatible model from the SAME engine when one exists. Legacy
    Ollama-name-resolution pools keep group-driven specs (skipped here).
    """
    if pools is None:
        pools = active_engine_pools()
    spec_names = {name for name, *_ in specs}
    for provider, pool in pools.items():
        engine = resolve_engine(provider)
        if not engine or engine.get("resolve_model"):
            continue  # legacy Ollama-name resolution pools keep group-driven specs
        chat_models = []
        for model in pool:
            if not isinstance(model, dict):
                continue
            if model.get("primary_category") in {"embedding", "reranker", "unknown"}:
                continue
            if model.get("model_type") not in (None, "llm", "vlm"):
                continue
            chat_models.append(model["name"])
        for index, model_name in enumerate(chat_models):
            slug = re.sub(r"[^a-z0-9]+", "-", model_name.lower()).strip("-")
            profile_name = f"local-{provider}-{slug}"
            if profile_name in spec_names:
                continue
            faster_ref = chat_models[index + 1] if index + 1 < len(chat_models) else ""
            specs.append(
                (
                    profile_name,
                    f"{provider}/{model_name}",
                    f"{provider}/{faster_ref}" if faster_ref else "",
                    provider,
                    provider,
                )
            )


def valid_profile_name(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name) and ".." not in name)


def prepare_profile_dir(target_dir: str, private_profiles: bool) -> Path:
    target = Path(target_dir).expanduser()
    if target.is_symlink():
        raise OSError("unsafe symlink at Junie models directory")
    home = Path.home()
    ai_dir = home / ".ai"
    junie_dir = home / ".junie"
    default_target = home / ".junie" / "models"
    if target.absolute() == default_target.absolute():
        if junie_dir.is_symlink() and junie_dir.resolve() != ai_dir.resolve():
            raise OSError("unsafe ~/.junie symlink target")
        if target.resolve() != (ai_dir / "models").resolve():
            raise OSError(
                "Junie model output is not the canonical ~/.ai/models directory"
            )
    if private_profiles:
        if getattr(os, "getuid", None) is None:
            raise OSError(
                "private Junie LiteLLM profiles require POSIX ownership controls"
            )
        expected = (ai_dir / "models").resolve()
        if ai_dir.is_symlink() or target.resolve() != expected:
            raise OSError(
                "LiteLLM profiles require the canonical ~/.junie/models directory"
            )
        if junie_dir.is_symlink() and junie_dir.resolve() != ai_dir.resolve():
            raise OSError("unsafe ~/.junie symlink target")
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    target_stat = target.stat()
    owner = getattr(os, "getuid", lambda: target_stat.st_uid)()
    if not stat.S_ISDIR(target_stat.st_mode) or target_stat.st_uid != owner:
        raise OSError("unsafe owner or type for Junie models directory")
    os.chmod(target, 0o700)
    return target


def atomic_private_write(path: Path, content: str) -> None:
    if path.is_symlink():
        raise OSError(f"unsafe symlink at Junie profile: {path.name}")
    fd, temp_path = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        os.chmod(temp_path, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(content)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def read_profile_manifest(target_dir: Path) -> set[str]:
    manifest = target_dir / PROFILE_MANIFEST
    if manifest.is_symlink():
        raise OSError("unsafe Junie profile manifest symlink")
    try:
        metadata = manifest.stat()
        owner = getattr(os, "getuid", lambda: None)()
        if (
            stat.S_IMODE(metadata.st_mode) != 0o600
            or (owner is not None and metadata.st_uid != owner)
            or not stat.S_ISREG(metadata.st_mode)
        ):
            raise OSError("unsafe Junie profile manifest owner or permissions")
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set()
    if not isinstance(data, list) or any(
        not isinstance(name, str)
        or name.endswith(".json")
        or not valid_profile_name(name)
        for name in data
    ):
        raise ValueError("invalid Junie generated-profile manifest")
    return set(data)


def cleanup_profiles(
    target_dir: Path,
    previous: set[str],
    generated: set[str],
    legacy_litellm: set[str],
    dry_run: bool,
) -> None:
    stale = (previous | legacy_litellm) - generated
    for name in sorted(stale):
        filename = name if name.endswith(".json") else f"{name}.json"
        if not valid_profile_name(filename[:-5]):
            continue
        path = target_dir / filename
        if not path.exists() and not path.is_symlink():
            continue
        if path.is_symlink() or not path.is_file():
            raise OSError(f"unsafe stale Junie profile path: {filename}")
        if dry_run:
            logger.info(f"Would remove stale generated profile: {filename}")
        else:
            path.unlink()
            logger.info(f"Removed stale generated profile: {filename}")


def atomic_write_manifest(target_dir: Path, names: set[str]) -> None:
    atomic_private_write(
        target_dir / PROFILE_MANIFEST,
        json.dumps(sorted(names), indent=2) + "\n",
    )


def main():
    parser = argparse.ArgumentParser(
        description="Generate JetBrains AI model profiles."
    )
    parser.add_argument(
        "--groups-json", required=True, help="Path to model-groups.json"
    )
    parser.add_argument("--target-dir", required=True, help="Path to ~/.junie/models")
    parser.add_argument(
        "--local-models", default="", help="Space-separated local Ollama models"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview without writing files"
    )
    add_model_override_args(parser)
    add_min_reasoning_embedding_arg(parser)
    args = parser.parse_args()

    # Load ~/.env so standalone runs see gate/endpoint vars (OMLX_* etc.);
    # inside make deploy / configure-jetbrains-ai.py the parent already
    # carries them.
    load_env()

    groups_path = os.path.abspath(os.path.expanduser(args.groups_json))
    target_dir = os.path.abspath(os.path.expanduser(args.target_dir))
    if not os.path.isfile(groups_path):
        logger.critical(f"Groups file does not exist: {groups_path}")
        raise SystemExit(1)
    try:
        with open(groups_path, encoding="utf-8") as file:
            cfg = json.load(file)
        registry = tier_registry.load_registry()
    except Exception as exc:
        logger.critical(f"Failed to read model configuration: {exc}")
        raise SystemExit(1)

    local_models = parse_local_models(args.local_models)
    if not local_models:
        local_models = [
            m if isinstance(m, dict) else str(m) for m in list_local_ollama_models()
        ]
    if any(
        engine_gate_active(provider)
        for provider in {
            model.get("provider")
            for model in local_models
            if isinstance(model, dict) and model.get("provider")
        }
    ):
        for provider_name in {
            model.get("provider")
            for model in local_models
            if isinstance(model, dict) and model.get("provider")
        }:
            engine = resolve_engine(provider_name)
            if (
                engine
                and engine_gate_active(provider_name)
                and engine.get("provider_config")
            ):
                cfg.setdefault("providers", {})[provider_name] = {
                    "baseUrl": f"{engine['base_url']().rstrip('/')}/v1",
                    "apiType": "OpenAICompletion",
                    "apiKeyEnv": engine["api_key_env"],
                }
    tier_specs = []
    for tier in registry.get("presets", {}):
        if not tier_registry.uses_local_placeholders(registry, tier):
            categories = {}
            roles = tier_registry.materialize_role_models(
                registry, tier, categories, args.role_models
            )
        else:
            tier_resolution_preset = args.local_fallback_preset or tier
            categories = {}
            if local_models:
                categories = tier_registry.classify_models_for_preset(
                    local_models,
                    registry,
                    tier_resolution_preset,
                    args.min_reasoning_embedding,
                )
                tier_registry.apply_placeholder_overrides(
                    categories, args.category_models
                )
            roles = tier_registry.materialize_role_models(
                registry, tier, categories, args.role_models
            )
        orchestrator_ref = roles.get("orchestrator", "")
        librarian_ref = roles.get("librarian", "")
        tier_specs.append(
            (
                tier,
                orchestrator_ref,
                librarian_ref,
                None,
                None,
            )
        )
    if args.local_fallback_preset:
        logger.info(f"Using local fallback preset: {args.local_fallback_preset}")

    litellm_gate = (
        os.environ.get(JUNIE_LITELLM_ENV, "0") == "1"
        and os.environ.get("DOTFILES_RUN_LITELLM_SETUP", "0") == "1"
    )
    junie_key = read_junie_key() if litellm_gate else ""
    if litellm_gate and not junie_key:
        logger.warning(
            "Junie LiteLLM key file unavailable; proxy-routed profiles will be omitted"
        )
    providers = build_provider_configs(cfg, junie_key)
    use_litellm = litellm_gate and bool(junie_key) and "litellm" in providers
    groups = select_model_groups(cfg.get("groups", {}), use_litellm)
    catalogue = None
    if use_litellm:
        catalogue = litellm_catalogue_models(providers["litellm"]["baseUrl"], junie_key)
        groups = filter_litellm_groups(groups, catalogue)
    specs = tier_specs + [
        (
            name,
            group.get("primaryModel", ""),
            group.get("fasterModel", ""),
            group.get("provider"),
            group.get("fasterProvider"),
        )
        for name, group in groups.items()
    ]
    # Selection parity: one spec per chat-capable pool model (registry-driven,
    # N-engine contract) so every local model is selectable in JetBrains AI.
    specs_before_pool = {name for name, *_ in specs}
    append_pool_profile_specs(specs)
    pool_profile_names = {name for name, *_ in specs} - specs_before_pool
    tier_names = set(registry.get("presets", {}))
    ollama_model_names = [
        model["name"] if isinstance(model, dict) else str(model)
        for model in local_models
        if not isinstance(model, dict) or model.get("provider", "ollama") == "ollama"
    ]
    temperatures = cfg.get("modelTemperatures", {})
    generated = set()
    generated_names = set()
    if args.dry_run:
        output_dir = Path(target_dir)
        previous_generated = set()
    else:
        output_dir = prepare_profile_dir(target_dir, use_litellm)
        previous_generated = read_profile_manifest(output_dir)
    logger.info("Generating JetBrains AI model profiles...")

    for name, primary_ref, faster_ref, explicit_provider, explicit_faster in specs:
        provider = explicit_provider or model_provider(primary_ref)
        is_routed_spec = name in tier_names or name in pool_profile_names
        route_primary = (
            litellm_gate and is_routed_spec and provider in LITELLM_ROUTED_PROVIDERS
        )
        route_faster_provider = explicit_faster or (
            model_provider(faster_ref) if faster_ref else provider
        )
        route_faster = (
            litellm_gate
            and is_routed_spec
            and faster_ref
            and route_faster_provider in LITELLM_ROUTED_PROVIDERS
        )
        if route_primary:
            if catalogue is None:
                logger.warning("Omitting %s: LiteLLM model catalogue unavailable", name)
                continue
            alias = litellm_alias(primary_ref, provider)
            if alias not in catalogue:
                logger.warning("Omitting %s: LiteLLM primary alias unavailable", name)
                continue
            provider = "litellm"
            primary_ref = alias
        if route_faster:
            faster_alias = litellm_alias(faster_ref, route_faster_provider)
            if catalogue is None or faster_alias not in catalogue:
                logger.info(
                    "Omitting faster model for %s: LiteLLM alias unavailable", name
                )
                faster_ref = ""
            else:
                faster_ref = faster_alias
                explicit_faster = "litellm"
        if provider == "litellm":
            if explicit_faster and explicit_faster != "litellm":
                logger.error(
                    f"LiteLLM profile {name} cannot use direct faster provider "
                    f"'{explicit_faster}'"
                )
                raise SystemExit(1)
            faster_provider = "litellm"
        else:
            faster_provider = explicit_faster or (
                model_provider(faster_ref) if faster_ref else provider
            )
        if not provider or provider not in providers:
            logger.warning(f"Unknown provider for {name} — skipping")
            continue
        if faster_provider not in providers:
            logger.warning(
                f"Unknown faster provider '{faster_provider}' for {name} — skipping"
            )
            continue
        if (
            provider == "github-copilot"
            and not os.environ.get("GITHUB_TOKEN", "").strip()
        ):
            logger.info(f"GITHUB_TOKEN not set — skipping experimental {name} group")
            continue
        engine = resolve_engine(provider)
        if engine and not local_models and not primary_ref.startswith("_local:"):
            logger.info(f"No local Ollama models available — skipping {name}")
            continue
        primary = model_id(primary_ref, provider)
        if (
            engine
            and engine.get("resolve_model")
            and not primary_ref.startswith("_local:")
        ):
            primary = resolve_model(primary, ollama_model_names)
        if primary_ref.startswith("_local:") or not primary:
            logger.warning(f"Could not resolve primary model for {name} — skipping")
            continue
        faster = model_id(faster_ref, faster_provider) if faster_ref else ""
        faster_engine = resolve_engine(faster_provider)
        if faster and faster_engine and faster_engine.get("resolve_model"):
            faster = resolve_model(faster, ollama_model_names)
        data = providers[provider].copy()
        data["id"] = primary
        primary_temperature = lookup_model_temperature(primary, temperatures)
        data["primaryModel"] = {
            "id": primary,
            "temperature": float(
                0.7 if primary_temperature is None else primary_temperature
            ),
        }
        if faster:
            faster_temperature = lookup_model_temperature(faster, temperatures)
            faster_data = {
                "id": faster,
                "temperature": float(
                    0.7 if faster_temperature is None else faster_temperature
                ),
            }
            if faster_provider != provider:
                faster_data.update(providers[faster_provider])
            data["fasterModel"] = faster_data
        output = json.dumps(data, indent=2) + "\n"
        if not valid_profile_name(name):
            logger.error("Unsafe generated Junie profile name; refusing to write")
            raise SystemExit(1)
        filename = f"{name}.json"
        path = output_dir / filename
        generated.add(filename)
        generated_names.add(name)
        if args.dry_run:
            logger.info(
                f"Would configure: {name} → primary={primary} faster={faster or 'none'}"
            )
        else:
            if path.is_symlink():
                raise OSError(f"unsafe symlink at Junie profile: {filename}")
            if path.exists():
                path_stat = path.stat()
                expected_owner = getattr(os, "getuid", lambda: None)()
                if (
                    expected_owner is not None and path_stat.st_uid != expected_owner
                ) or not stat.S_ISREG(path_stat.st_mode):
                    raise OSError(f"unsafe owner or type for Junie profile: {filename}")
                existing_output = path.read_text(encoding="utf-8")
            else:
                existing_output = None
            if existing_output == output:
                os.chmod(path, 0o600)
                logger.info(f"Unchanged: {name} ({primary})")
                continue
            atomic_private_write(path, output)
            logger.info(
                f"Configured: {name} → primary={primary} faster={faster or 'none'}"
            )

    legacy_litellm = {
        name
        for name, group in cfg.get("groups", {}).items()
        if group.get("provider") == "litellm"
    }
    if output_dir.is_dir():
        cleanup_profiles(
            output_dir,
            previous_generated,
            generated_names,
            legacy_litellm,
            args.dry_run,
        )
        if not args.dry_run:
            atomic_write_manifest(output_dir, generated_names)
    logger.info(f"JetBrains AI models configured at {target_dir}")
    if not args.dry_run:
        try:
            from model_stamp import write_stamp

            # Stamp marks last model sync for check-model-drift staleness warning.
            write_stamp()
        except Exception as exc:
            logger.warning(f"Could not write model sync stamp: {exc}")


if __name__ == "__main__":
    main()
