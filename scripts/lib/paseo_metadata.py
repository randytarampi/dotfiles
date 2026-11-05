"""Resolve Paseo metadata's exact local orchestrator from the shared tier registry."""

import ipaddress
from urllib.parse import urlsplit

import tier_registry


def loopback_url(value):
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "http" or parsed.username or parsed.password:
            return False
        host = parsed.hostname
        if host == "localhost":
            return True
        return bool(host and ipaddress.ip_address(host).is_loopback)
    except (ValueError, TypeError):
        return False


def resolve_orchestrator(registry, preset, discovered, *, pi_settings, pi_models):
    """Fail closed unless current Pi advertises the same actual local model."""
    if not tier_registry.uses_local_placeholders(registry, preset):
        raise ValueError("selected preset does not assign a local orchestrator")
    if not isinstance(discovered, list) or not discovered:
        raise ValueError("local model discovery is unavailable")
    category = tier_registry.classify_models_for_preset(
        discovered,
        registry,
        preset,
        profile=tier_registry.get_profile_name(registry, preset),
    )
    roles = tier_registry.materialize_role_models(registry, preset, category)
    selected = roles.get("orchestrator")
    if not isinstance(selected, str) or "/" not in selected:
        raise ValueError("orchestrator unresolved")
    provider, model = selected.split("/", 1)
    if (
        provider not in {"omlx", "ollama"}
        or not model
        or model == "no-model-available"
        or model.startswith("_local:")
    ):
        raise ValueError("orchestrator is not a qualified local model")
    if not isinstance(pi_models, dict) or not isinstance(pi_settings, dict):
        raise ValueError("Pi config is not an object")
    providers = pi_models.get("providers", {})
    if not isinstance(providers, dict):
        raise ValueError("Pi provider registry is invalid")
    config = providers.get(provider)
    if not isinstance(config, dict) or not loopback_url(config.get("baseUrl")):
        raise ValueError("Pi local provider is unavailable or not loopback")
    actual_models = config.get("models", [])
    if not isinstance(actual_models, list) or model not in [
        item.get("id") for item in actual_models if isinstance(item, dict)
    ]:
        raise ValueError("Pi does not advertise the resolved orchestrator")
    if (
        pi_settings.get("defaultProvider") != provider
        or pi_settings.get("defaultModel") != model
    ):
        raise ValueError("Pi effective default disagrees with the local orchestrator")
    return selected


def metadata_changes(config, selected):
    if not isinstance(config, dict):
        raise ValueError("Paseo config must be an object")
    agents = config.get("agents", {})
    if not isinstance(agents, dict):
        raise ValueError("agents must be an object")
    metadata = agents.get("metadataGeneration", {})
    if not isinstance(metadata, dict):
        raise ValueError("agents.metadataGeneration must be an object")
    existing = metadata.get("providers")
    desired = [{"provider": "pi", "model": selected}]
    if existing is not None and existing != desired:
        raise ValueError("existing metadata provider list is user-owned")
    return [] if existing == desired else desired


def private_json(path):
    """Read existing private Pi state; never follow a symlink or print secrets."""
    import os
    import stat

    if path.is_symlink() or not path.is_file():
        raise ValueError("Pi config must be an existing regular file")
    info = path.stat()
    if (
        info.st_uid != os.getuid()
        or info.st_nlink != 1
        or not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) & 0o022
    ):
        raise ValueError("Pi config must be owner-controlled and not group-writable")
    from json import loads

    def unique(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise ValueError("duplicate Pi JSON key")
            result[name] = value
        return result

    data = loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    if not isinstance(data, dict):
        raise ValueError("Pi JSON root must be an object")
    return data
