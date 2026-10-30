#!/usr/bin/env python3
"""Generate and synchronize JetBrains model profiles."""

from __future__ import annotations

import argparse
import hashlib
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

import logger  # noqa: E402 -- scripts/lib is added to sys.path above.
import tier_registry  # noqa: E402 -- scripts/lib is added to sys.path above.
from unavailable_presets import DISABLED_PRESETS  # noqa: E402
from model_catalogues import (  # noqa: E402 -- scripts/lib bootstrap.
    MODEL_CATALOGUE_TIMEOUT,
    open_same_origin,
)
from litellm_aliases import (  # noqa: E402 -- scripts/lib bootstrap.
    LITELLM_ROUTED_PROVIDERS,
)
from ai_models import (  # noqa: E402 -- scripts/lib bootstrap.
    is_specialized_model_name,
    resolve_model,
)  # noqa: E402 -- scripts/lib bootstrap.
from cli_helpers import (  # noqa: E402 -- scripts/lib bootstrap.
    add_model_override_args,
    add_min_reasoning_embedding_arg,
)  # noqa: E402 -- scripts/lib bootstrap.
from constants import (  # noqa: E402 -- scripts/lib bootstrap.
    MERIDIAN_DEFAULT_HOST,
    MERIDIAN_DEFAULT_PORT,
    get_litellm_proxy_mode,
    get_ollama_local_base_url,
)
from discover_models import (  # noqa: E402 -- scripts/lib bootstrap.
    list_local_ollama_models,
)  # noqa: E402 -- scripts/lib bootstrap.
from env import load_env  # noqa: E402 -- scripts/lib bootstrap.
from local_engines import (  # noqa: E402 -- scripts/lib bootstrap.
    active_engine_pools,
    engine_gate_active,
    local_endpoint_for,
    resolve_engine,
)
from free_preset_registry import PRESETS as DYNAMIC_FREE_PRESETS  # noqa: E402
from free_preset_runtime import prepare_dynamic_free_registry  # noqa: E402

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
    groups = derive_litellm_groups(groups)
    selected = {}
    for name, group in groups.items():
        provider = group.get("provider")
        if provider == "litellm":
            if group.get("gateway_alias"):
                selected[name] = group
            continue
        if provider in LITELLM_ROUTED_PROVIDERS:
            routed_name = f"litellm-{name}"
            if routed_name in groups:
                selected[routed_name] = groups[routed_name]
            else:
                logger.error(
                    "Routed Junie group %s failed to derive %s", name, routed_name
                )
            continue
        selected[name] = group
    return selected


def _twin_for_group(name, group):
    provider = group.get("provider")
    twin_name = f"litellm-{name}"
    twin = {"provider": "litellm"}
    for field in ("primaryModel", "fasterModel"):
        model = group.get(field)
        if not model:
            continue
        model_provider_name = (
            group.get("fasterProvider", provider)
            if field == "fasterModel"
            else provider
        )
        if (
            field == "fasterModel"
            and model_provider_name not in LITELLM_ROUTED_PROVIDERS
        ):
            logger.warning(
                "Routed Junie group %s: faster provider %s is not gateway-routed; omitting the faster model from %s",
                name,
                model_provider_name,
                twin_name,
            )
            continue
        if group.get("gateway_alias") or model_provider_name == "litellm":
            twin[field] = model
        else:
            twin[field] = litellm_alias(model, model_provider_name)
    if group.get("fasterModel") and "fasterModel" in twin:
        twin["fasterProvider"] = "litellm"
    return twin_name, twin


def _merge_twin(derived, groups, name, twin_name, twin):
    existing = groups.get(twin_name)
    if existing and existing != twin:
        logger.error("Derived Junie twin %s diverges from %s", twin_name, name)
        return
    derived[twin_name] = twin


def derive_litellm_groups(groups: dict) -> dict:
    """Build gateway twins for routed plain groups, preserving exact model IDs."""
    derived = {
        name: group
        for name, group in groups.items()
        if name not in DISABLED_PRESETS
        or name.removeprefix("litellm-") in DYNAMIC_FREE_PRESETS
    }
    for name, group in groups.items():
        if name not in derived:
            continue
        if group.get("provider") not in LITELLM_ROUTED_PROVIDERS or name.startswith(
            "litellm-"
        ):
            continue
        twin_name, twin = _twin_for_group(name, group)
        _merge_twin(derived, groups, name, twin_name, twin)
    return derived


def routed_group_violations(
    groups: dict, derived_groups: dict | None = None
) -> list[str]:
    derived_groups = (
        derived_groups if derived_groups is not None else derive_litellm_groups(groups)
    )
    errors = []
    for name, group in groups.items():
        if (
            name.startswith("litellm-")
            or group.get("provider") not in LITELLM_ROUTED_PROVIDERS
        ):
            continue
        twin_name = f"litellm-{name}"
        expected = derive_litellm_groups({name: group}).get(twin_name)
        if not expected or derived_groups.get(twin_name) != expected:
            errors.append(f"Routed Junie group {name} has no matching twin {twin_name}")
    return errors


def litellm_catalogue_models(base_url: str, api_key: str) -> set[str] | None:
    """Read LiteLLM's model catalogue; None means availability is unknown."""
    try:
        parsed = urllib.parse.urlsplit(base_url)
        port = parsed.port
        if parsed.scheme != "http":
            return None
        if parsed.hostname != "127.0.0.1":
            return None
        if port is None or not 1 <= port <= 65535:
            return None
        if parsed.username or parsed.password:
            return None
        if parsed.query or parsed.fragment:
            return None
        origin = f"http://127.0.0.1:{port}"
        request = urllib.request.Request(
            f"{origin}/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            method="GET",
        )
        with open_same_origin(request, timeout=MODEL_CATALOGUE_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            return None
        ids = set()
        for entry in data:
            if not isinstance(entry, dict):
                return None
            model_id = entry.get("id")
            if not isinstance(model_id, str):
                return None
            ids.add(model_id)
        return ids
    except Exception:
        logger.warning(
            "LiteLLM model catalogue unavailable; exact model availability is unknown"
        )
        return None


def filter_litellm_groups(groups: dict, available_models: set[str] | None) -> dict:
    """Filter gateway groups against the live LiteLLM catalogue.

    Fail-closed: when catalogue availability is unknown, every gateway
    (``provider == "litellm"``) group is omitted with a diagnostic so no
    unverified alias is published; direct-provider groups are preserved. With a
    known catalogue the primary alias is required and the faster alias is
    validated independently: an unavailable faster alias drops only the faster
    fields from an otherwise usable group.
    """
    if available_models is None:
        omitted = [
            name for name, group in groups.items() if group.get("provider") == "litellm"
        ]
        if omitted:
            logger.warning(
                "LiteLLM model catalogue unavailable; omitting gateway groups: %s",
                ", ".join(sorted(omitted)),
            )
        return {
            name: group
            for name, group in groups.items()
            if group.get("provider") != "litellm"
        }
    filtered = {}
    for name, group in groups.items():
        if group.get("provider") != "litellm":
            filtered[name] = group
            continue
        if group.get("primaryModel", "") not in available_models:
            logger.warning("Omitting %s: LiteLLM primary alias unavailable", name)
            continue
        faster = group.get("fasterModel", "")
        if faster and faster not in available_models:
            logger.info("Omitting faster model for %s: LiteLLM alias unavailable", name)
            group = {
                key: value
                for key, value in group.items()
                if key not in ("fasterModel", "fasterProvider")
            }
        filtered[name] = group
    return filtered


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
    model_name = resolve_canonical_temperature_ref(model_name)
    matches = [p for p in overrides if model_name.lower().startswith(p.lower())]
    return overrides[max(matches, key=len)] if matches else None


def temperature_identity(group_name: str) -> str:
    return group_name.removeprefix("litellm-")


def resolve_canonical_temperature_ref(model_ref: str) -> str:
    model = model_ref.removeprefix("litellm/")
    for prefix in ("google/models/", "openrouter/", "openai/", "ollama-cloud/"):
        if model.startswith(prefix):
            return model[len(prefix) :]
    return model.split("/", 1)[-1]


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
            if model.get("primary_category") in {
                "embedding",
                "reranker",
                "unknown",
                "audio",
                "decision",
                "conversion",
            }:
                continue
            if model.get("model_type") not in (None, "llm", "vlm"):
                continue
            # Known specialized models never become chat profiles — the
            # name-level guard covers untyped pool records (Ollama discovery
            # emits name/size/provider only, so typed filters cannot see
            # decision/audio/embedding/conversion models in that shape).
            if is_specialized_model_name(model.get("name", "")):
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


def prepare_profile_dir(
    target_dir: str, private_profiles: bool, dry_run: bool = False
) -> Path:
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
    if dry_run and not target.exists():
        return target
    if not dry_run:
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
    target_stat = target.stat()
    owner = getattr(os, "getuid", lambda: target_stat.st_uid)()
    if not stat.S_ISDIR(target_stat.st_mode) or target_stat.st_uid != owner:
        raise OSError("unsafe owner or type for Junie models directory")
    if not dry_run:
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


def manifest_path() -> Path:
    """Return the profile-index location OUTSIDE ~/.junie/models.

    Junie loads every .json file in the models directory as a profile
    object; the index (a JSON array) must live elsewhere.
    """
    return Path.home() / ".local/share/dotfiles/jetbrains-profiles-manifest.json"


def read_profile_manifest(target_dir: Path) -> dict[str, str]:
    manifest = manifest_path()
    legacy = target_dir / PROFILE_MANIFEST
    if manifest.is_symlink() or legacy.is_symlink():
        raise OSError("unsafe Junie profile manifest symlink")
    try:
        legacy.lstat()
    except FileNotFoundError:
        pass
    else:
        raise OSError(
            f"legacy Junie profile manifest blocks generation: {legacy}; "
            "back it up privately, review its contents, then relocate it manually "
            f"outside the models directory before retrying; do not overwrite {manifest}; "
            "if a canonical ledger exists, stop and reconcile both privately"
        )
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
        return {}
    # Legacy name-only manifests are deliberately not trusted for mutation.
    if isinstance(data, list):
        return {}
    if not isinstance(data, dict) or data.get("schema") != 2:
        return {}
    if data.get("target") != str(target_dir.resolve()):
        raise ValueError("Junie profile manifest target directory mismatch")
    data = data.get("profiles")
    if not isinstance(data, dict) or any(
        not isinstance(name, str)
        or not valid_profile_name(name)
        or not isinstance(digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        for name, digest in data.items()
    ):
        raise ValueError("invalid Junie generated-profile manifest")
    return data


def cleanup_profiles(
    target_dir: Path,
    previous: dict[str, str],
    generated: set[str],
    legacy_litellm: set[str],
    dry_run: bool,
) -> None:
    stale = set(previous) - generated
    for name in sorted(stale):
        filename = f"{name}.json"
        if not valid_profile_name(filename[:-5]):
            continue
        path = target_dir / filename
        if not path.exists() and not path.is_symlink():
            continue
        if path.is_symlink() or not path.is_file():
            raise OSError(f"unsafe stale Junie profile path: {filename}")
        expected = previous.get(name)
        if (
            expected is None
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected
        ):
            logger.warning(
                "Preserving stale profile without matching ownership digest: %s",
                filename,
            )
            continue
        if dry_run:
            logger.info(f"Would remove stale generated profile: {filename}")
        else:
            path.unlink()
            logger.info(f"Removed stale generated profile: {filename}")


def atomic_write_manifest(target_dir: Path, names: dict[str, str]) -> None:
    target = manifest_path()
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    atomic_private_write(
        target,
        json.dumps(
            {"schema": 2, "target": str(target_dir.resolve()), "profiles": names},
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )


def resolve_model_groups(
    cfg, registry, dynamic_registry=None, use_litellm=False, available_dynamic=None
):
    """Resolve only explicitly requested shared presets and compatibility names."""
    required = cfg.get("requiredGroups", [])
    if not isinstance(required, list) or any(
        not isinstance(name, str) for name in required
    ):
        raise ValueError("requiredGroups must be a list of group names")
    known = set(cfg.get("groups", {})) | set(cfg.get("sharedPresets", {}))
    known.update(cfg.get("compatibility", {}))
    unknown = set(required) - known
    if unknown:
        raise ValueError(f"Unknown required Junie groups: {', '.join(sorted(unknown))}")
    if available_dynamic is None:
        available_dynamic = (
            set(DYNAMIC_FREE_PRESETS) if dynamic_registry is not None else set()
        )
    else:
        available_dynamic = set(available_dynamic)
    requested_disabled = (
        set(cfg.get("sharedPresets", {})) | set(cfg.get("groups", {}))
    ) & DISABLED_PRESETS
    requested_disabled -= {
        name for name in DYNAMIC_FREE_PRESETS if name in available_dynamic
    }
    if requested_disabled:
        logger.warning(
            "Omitting unavailable Junie groups: %s",
            ", ".join(sorted(requested_disabled)),
        )
    requested_groups = dict(cfg.get("groups", {}))
    for name in DYNAMIC_FREE_PRESETS:
        if name in requested_groups and name not in available_dynamic:
            requested_groups.pop(name)
    groups = {
        name: dict(group)
        for name, group in requested_groups.items()
        if name not in DISABLED_PRESETS
        or (name in DYNAMIC_FREE_PRESETS and name in available_dynamic)
    }
    for name, preset in cfg.get("sharedPresets", {}).items():
        if name in DISABLED_PRESETS and name not in available_dynamic:
            continue
        if name != preset:
            raise ValueError("Shared group must retain its canonical preset name")
        role_registry = dynamic_registry if preset in DYNAMIC_FREE_PRESETS else registry
        if role_registry is None or (
            preset in DYNAMIC_FREE_PRESETS and preset not in available_dynamic
        ):
            continue
        roles = tier_registry.materialize_role_models(role_registry, preset, {})
        primary, faster = roles["orchestrator"], roles["librarian"]
        is_gateway_alias = preset in DYNAMIC_FREE_PRESETS and use_litellm
        if not all(
            isinstance(ref, str) and ref and (is_gateway_alias or "/" in ref)
            for ref in (primary, faster)
        ):
            raise ValueError("Shared group has unresolved model assignments")
        if is_gateway_alias:
            provider, model = "litellm", primary
            faster_provider, faster_model = "litellm", faster
        else:
            provider, model = primary.split("/", 1)
            faster_provider, faster_model = faster.split("/", 1)
        groups[name] = {
            "provider": provider,
            "primaryModel": model,
            "fasterProvider": faster_provider,
            "fasterModel": faster_model,
            **({"gateway_alias": True} if is_gateway_alias else {}),
        }
    for alias, canonical in cfg.get("compatibility", {}).items():
        dynamic_canonical = canonical.removeprefix("litellm-")
        if alias in DISABLED_PRESETS and alias not in DYNAMIC_FREE_PRESETS:
            continue
        if (
            dynamic_canonical in DYNAMIC_FREE_PRESETS
            and dynamic_canonical not in available_dynamic
        ):
            if alias in required:
                raise ValueError(f"Required Junie group unavailable: {alias}")
            continue
        if canonical in DISABLED_PRESETS and dynamic_canonical not in available_dynamic:
            continue
        if alias in groups or canonical not in groups:
            raise ValueError(f"Invalid compatibility group: {alias}")
        groups[alias] = dict(groups[canonical])
    unavailable_required = set(required) - set(groups)
    if unavailable_required:
        raise ValueError(
            "Required Junie groups unavailable: "
            + ", ".join(sorted(unavailable_required))
        )
    return groups


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
    args.local_fallback_preset = args.local_fallback_preset or os.environ.get(
        "DOTFILES_LOCAL_FALLBACK_PRESET"
    )
    if (
        args.local_fallback_preset in DISABLED_PRESETS
        and args.local_fallback_preset not in DYNAMIC_FREE_PRESETS
    ):
        parser.error(f"Preset '{args.local_fallback_preset}' is unavailable")
    if args.local_fallback_preset in DYNAMIC_FREE_PRESETS:
        parser.error("Dynamic free presets cannot be local fallback presets")

    groups_path = os.path.abspath(os.path.expanduser(args.groups_json))
    target_dir = os.path.abspath(os.path.expanduser(args.target_dir))
    if not os.path.isfile(groups_path):
        logger.critical(f"Groups file does not exist: {groups_path}")
        raise SystemExit(1)
    try:
        with open(groups_path, encoding="utf-8") as file:
            cfg = json.load(file)
        registry = tier_registry.load_registry()
        requested_dynamic = {
            preset
            for preset in DYNAMIC_FREE_PRESETS
            if preset in cfg.get("sharedPresets", {}).values()
            or preset in cfg.get("groups", {})
        }
        required_groups = cfg.get("requiredGroups", [])
        if not isinstance(required_groups, list) or any(
            not isinstance(name, str) for name in required_groups
        ):
            raise ValueError("requiredGroups must be a list of group names")
        known_groups = (
            set(cfg.get("groups", {}))
            | set(cfg.get("sharedPresets", {}))
            | set(cfg.get("compatibility", {}))
        )
        unknown_required = set(required_groups) - known_groups
        if unknown_required:
            raise ValueError(
                "Unknown required Junie groups: " + ", ".join(sorted(unknown_required))
            )
        required_groups = cfg.get("requiredGroups", [])
        required_dynamic = set()
        for group_name in required_groups:
            preset = cfg.get("sharedPresets", {}).get(group_name, group_name)
            preset = cfg.get("compatibility", {}).get(preset, preset)
            if preset in DYNAMIC_FREE_PRESETS:
                required_dynamic.add(preset)
        has_model_overrides = bool(args.role_models or args.category_models)
        if required_dynamic and has_model_overrides:
            raise ValueError("Dynamic free preset overrides are refused")
        if has_model_overrides:
            # Optional dynamic groups may be omitted while ordinary profile
            # overrides are active; required groups remain fail-closed above.
            requested_dynamic.clear()
        dynamic_registry = None
        available_dynamic = set()
        full_registry = isinstance(registry.get("_tiers"), dict) and all(
            preset in registry.get("presets", {}) for preset in requested_dynamic
        )
        if requested_dynamic and full_registry:
            dynamic_registry = json.loads(json.dumps(registry))
            for preset in sorted(requested_dynamic):
                try:
                    resolved_registry, _ = prepare_dynamic_free_registry(
                        registry, [preset], os.environ, consumer="junie"
                    )
                except Exception:
                    if preset in required_dynamic:
                        raise
                    logger.warning("Optional Junie free group unavailable: %s", preset)
                    continue
                dynamic_registry["presets"][preset] = resolved_registry["presets"][
                    preset
                ]
                dynamic_registry["_tiers"][preset] = resolved_registry["_tiers"][preset]
                available_dynamic.add(preset)
        cfg["groups"] = resolve_model_groups(
            cfg,
            registry,
            dynamic_registry,
            use_litellm=os.environ.get("DOTFILES_USE_LITELLM_PROXY", "0") == "1",
            available_dynamic=available_dynamic,
        )
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
    for tier in list(registry.get("presets", {})):
        if tier in DYNAMIC_FREE_PRESETS and tier not in available_dynamic:
            continue
        if tier in DISABLED_PRESETS and tier not in DYNAMIC_FREE_PRESETS:
            continue
        tier_registry_source = (
            dynamic_registry if tier in DYNAMIC_FREE_PRESETS else registry
        )
        if tier in DYNAMIC_FREE_PRESETS and dynamic_registry is not None:
            registry["presets"][tier] = dynamic_registry["presets"][tier]
            registry["_tiers"][tier] = dynamic_registry["_tiers"][tier]
        if tier in DYNAMIC_FREE_PRESETS and dynamic_registry is not None:
            resolved = tier_registry_source["presets"][tier]
            roles = {
                "orchestrator": resolved["orchestrator"]["model"],
                "librarian": resolved["librarian"]["model"],
            }
        elif not tier_registry.uses_local_placeholders(tier_registry_source, tier):
            categories = {}
            roles = tier_registry.materialize_role_models(
                tier_registry_source, tier, categories, args.role_models
            )
        else:
            tier_resolution_preset = args.local_fallback_preset or tier
            categories = {}
            if local_models:
                categories = tier_registry.classify_models_for_preset(
                    local_models,
                    tier_registry_source,
                    tier_resolution_preset,
                    args.min_reasoning_embedding,
                    profile=tier_registry.get_profile_name(registry, tier),
                )
                tier_registry.apply_placeholder_overrides(
                    categories, args.category_models
                )
            roles = tier_registry.materialize_role_models(
                tier_registry_source, tier, categories, args.role_models
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
        if tier in DYNAMIC_FREE_PRESETS:
            registry["presets"][tier] = tier_registry_source["presets"][tier]
    if args.local_fallback_preset:
        logger.info(f"Using local fallback preset: {args.local_fallback_preset}")

    litellm_gate = get_litellm_proxy_mode()
    junie_key = read_junie_key() if litellm_gate else ""
    if litellm_gate and not junie_key:
        logger.warning(
            "Junie LiteLLM key file unavailable; proxy-routed profiles will be omitted"
        )
    providers = build_provider_configs(cfg, junie_key)
    use_litellm = litellm_gate and bool(junie_key) and "litellm" in providers
    groups = select_model_groups(cfg.get("groups", {}), use_litellm)
    if litellm_gate and not use_litellm:
        groups = {
            name: group
            for name, group in groups.items()
            if group.get("provider") not in LITELLM_ROUTED_PROVIDERS
            and group.get("provider") != "litellm"
        }
    catalogue = None
    if use_litellm:
        catalogue = litellm_catalogue_models(providers["litellm"]["baseUrl"], junie_key)
        groups = filter_litellm_groups(groups, catalogue)
    specs_temperature_identity = {
        f"litellm-{name}": group.get("primaryModel", "")
        for name, group in cfg.get("groups", {}).items()
        if group.get("provider") in LITELLM_ROUTED_PROVIDERS
    }
    # Explicit groups own their names even when routing filters omit them;
    # never substitute a same-named registry tier for a frozen assignment.
    group_names = set(cfg.get("groups", {}))
    specs = [spec for spec in tier_specs if spec[0] not in group_names] + [
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
    names = [spec[0] for spec in specs]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate Junie output names; refusing generation")
    pool_profile_names = {name for name, *_ in specs} - specs_before_pool
    tier_names = set(registry.get("presets", {}))
    ollama_model_names = [
        model["name"] if isinstance(model, dict) else str(model)
        for model in local_models
        if not isinstance(model, dict) or model.get("provider", "ollama") == "ollama"
    ]
    temperatures = cfg.get("modelTemperatures", {})
    required_groups = set(cfg.get("requiredGroups", []))
    generated = set()
    generated_digests = {}
    generated_names = set()
    profile_plan = []
    for name, primary_ref, faster_ref, explicit_provider, explicit_faster in specs:
        if not isinstance(primary_ref, str) or not primary_ref:
            if name in required_groups:
                raise ValueError(f"Required Junie group unavailable: {name}")
            continue
        temperature_ref = specs_temperature_identity.get(name, primary_ref)
        provider = explicit_provider or model_provider(primary_ref)
        is_gateway_alias_spec = explicit_provider == "litellm" and name in group_names
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
        if route_primary and not is_gateway_alias_spec:
            alias = litellm_alias(primary_ref, provider)
            if catalogue is None or alias not in catalogue:
                if name in required_groups:
                    raise ValueError(f"Required Junie group unavailable: {name}")
                continue
            provider, primary_ref = "litellm", alias
        if route_faster and not is_gateway_alias_spec:
            faster_alias = litellm_alias(faster_ref, route_faster_provider)
            if catalogue is None or faster_alias not in catalogue:
                faster_ref = ""
            else:
                faster_ref, explicit_faster = faster_alias, "litellm"
        faster_provider = (
            "litellm"
            if provider == "litellm"
            else explicit_faster
            or (model_provider(faster_ref) if faster_ref else provider)
        )
        if (
            not provider
            or provider not in providers
            or faster_provider not in providers
        ):
            if name in required_groups:
                raise ValueError(f"Required Junie group unavailable: {name}")
            continue
        if (
            provider == "github-copilot"
            and not os.environ.get("GITHUB_TOKEN", "").strip()
        ):
            if name in required_groups:
                raise ValueError(f"Required Junie group unavailable: {name}")
            continue
        engine = resolve_engine(provider)
        if engine and not local_models and not primary_ref.startswith("_local:"):
            if name in required_groups:
                raise ValueError(f"Required Junie group unavailable: {name}")
            continue
        primary = model_id(primary_ref, provider)
        if (
            engine
            and engine.get("resolve_model")
            and not primary_ref.startswith("_local:")
        ):
            primary = resolve_model(primary, ollama_model_names)
        if primary_ref.startswith("_local:") or not primary:
            if name in required_groups:
                raise ValueError(f"Required Junie group unavailable: {name}")
            continue
        faster = model_id(faster_ref, faster_provider) if faster_ref else ""
        faster_engine = resolve_engine(faster_provider)
        if faster and faster_engine and faster_engine.get("resolve_model"):
            faster = resolve_model(faster, ollama_model_names)
        data = providers[provider].copy()
        data["id"] = primary
        primary_temperature = lookup_model_temperature(
            resolve_canonical_temperature_ref(temperature_ref), temperatures
        )
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
        if not valid_profile_name(name):
            raise ValueError("Unsafe generated Junie profile name; refusing to write")
        output = json.dumps(data, indent=2) + "\n"
        profile_plan.append((name, primary, faster, output))

    output_dir = Path(target_dir).expanduser()
    try:
        previous_generated = read_profile_manifest(output_dir)
    except OSError as exc:
        logger.critical(str(exc))
        raise SystemExit(1)
    try:
        for name, _primary, _faster, _output in profile_plan:
            path = output_dir / f"{name}.json"
            if path.is_symlink():
                raise OSError(f"unsafe symlink at Junie profile: {path.name}")
            if path.exists():
                if not path.is_file():
                    raise OSError(
                        f"unsafe owner or type for Junie profile: {path.name}"
                    )
                expected_owner = getattr(os, "getuid", lambda: None)()
                if expected_owner is not None and path.stat().st_uid != expected_owner:
                    raise OSError(f"unsafe owner for Junie profile: {path.name}")
                owned_digest = previous_generated.get(name)
                if owned_digest is None:
                    if name in required_groups:
                        raise OSError(
                            f"Required Junie profile is unmanaged: {path.name}"
                        )
                elif hashlib.sha256(path.read_bytes()).hexdigest() != owned_digest:
                    raise OSError(
                        f"modified managed Junie profile; refusing overwrite: {path.name}"
                    )
    except OSError as exc:
        logger.critical(str(exc))
        raise SystemExit(1)
    try:
        for name, expected_digest in previous_generated.items():
            path = output_dir / f"{name}.json"
            if path.is_symlink():
                raise OSError(f"unsafe stale Junie profile path: {path.name}")
            if path.exists() and (
                not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected_digest
            ):
                raise OSError(
                    f"modified managed Junie profile; refusing overwrite: {path.name}"
                )
    except OSError as exc:
        logger.critical(str(exc))
        raise SystemExit(1)
    try:
        output_dir = prepare_profile_dir(target_dir, use_litellm, args.dry_run)
    except OSError as exc:
        logger.critical(str(exc))
        raise SystemExit(1)
    logger.info("Generating JetBrains AI model profiles...")

    for name, primary, faster, output in profile_plan:
        filename = f"{name}.json"
        path = output_dir / filename
        if path.exists() and name not in previous_generated:
            logger.info("Preserving unmanaged Junie profile collision: %s", filename)
            continue
        elif args.dry_run and name in previous_generated:
            logger.info("Would create generated Junie profile: %s", filename)
        generated.add(name)
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
                generated_digests[name] = hashlib.sha256(output.encode()).hexdigest()
                os.chmod(path, 0o600)
                logger.info(f"Unchanged: {name} ({primary})")
                continue
        if not args.dry_run:
            atomic_private_write(path, output)
            generated_digests[name] = hashlib.sha256(output.encode()).hexdigest()
            logger.info(
                f"Configured: {name} → primary={primary} faster={faster or 'none'}"
            )

    legacy_litellm = set()
    if output_dir.is_dir():
        if not litellm_gate or (use_litellm and catalogue is not None):
            cleanup_profiles(
                output_dir,
                previous_generated,
                generated_names,
                legacy_litellm,
                args.dry_run,
            )
        else:
            # Unavailable credentials/catalogues cannot prove a profile stale.
            generated_digests = {**previous_generated, **generated_digests}
        if not args.dry_run:
            atomic_write_manifest(output_dir, generated_digests)
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
