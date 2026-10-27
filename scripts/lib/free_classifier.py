"""Preset audit using the executable classifier owned by litellm_config."""

from __future__ import annotations

import json
import argparse
import importlib
import importlib.util
import sys
from pathlib import Path

from litellm_config import classify_free_route
from provider_endpoints import PROVIDER_ENDPOINTS


def load_classifier(reference="litellm_config:classify_free_route"):
    module_name, symbol = reference.split(":", 1)
    return getattr(importlib.import_module(module_name), symbol)


def _routes(node, role="unknown", source="preset"):
    found = []
    if isinstance(node, dict):
        model = node.get("model")
        provider = node.get("provider")
        if isinstance(model, str):
            if "/" in model:
                host, model_id = "unknown", model
            elif provider:
                host, model_id = "unknown", f"{provider}/{model}"
            else:
                host, model_id = "unknown", model
            found.append((model_id, host, role, source))
        for key, value in node.items():
            if (
                key not in {"model", "provider"}
                and isinstance(value, str)
                and "/" in value
            ):
                found.append((value, "unknown", key, source))
            if key in {"model", "provider"} or not isinstance(value, (dict, list)):
                continue
            found.extend(
                _routes(
                    value,
                    role=key if key != "presets" else role,
                    source="fallback" if key == "fallback" else source,
                )
            )
    elif isinstance(node, list):
        for value in node:
            if isinstance(value, str):
                found.append((value, "unknown", role, source))
            else:
                found.extend(_routes(value, role, source))
    return found


def collect_routes(slim, junie_profiles=(), model_list=(), provider_configs=None):
    """Collect selections, not proof of deployment; gateway aliases need wire records."""
    records = []
    for preset in ("free", "omo-slim-opencode-zen-free"):
        records.extend(_routes(slim.get("presets", {}).get(preset, {}), source=preset))
        tier = slim.get("_tiers", {}).get(preset, {})
        records.extend(_routes(tier.get("council", {}), source=f"{preset}.council"))
        records.extend(_routes(tier.get("fallback", {}), source=f"{preset}.fallback"))
    for profile in junie_profiles:
        for name, group in profile.get("groups", {}).items():
            for field in ("primaryModel", "fasterModel"):
                provider = group.get("provider", "unknown")
                if field == "fasterModel":
                    provider = group.get("fasterProvider", provider)
                model = group.get(field)
                if model:
                    reference = (
                        model if provider == "litellm" else f"{provider}/{model}"
                    )
                    endpoint = profile.get("providers", {}).get(provider, {})
                    host = (
                        "unknown"
                        if provider == "litellm"
                        else _endpoint_host(endpoint.get("baseUrl"))
                    )
                    records.append((reference, host, field, f"junie.{name}"))
    resolved = []
    for model_id, host, role, source in records:
        direct_junie = source.startswith("junie.") and not source.startswith(
            "junie.litellm-"
        )
        aliases = (
            [
                item
                for item in model_list
                if isinstance(item, dict) and item.get("model_name") == model_id
            ]
            if not direct_junie
            else []
        )
        if aliases:
            # Audit every backend in a load-balanced alias, not only the last one.
            for alias in aliases:
                resolved.append((model_id, _resolved_host(alias), role, source))
        else:
            if host == "unknown" and not source.startswith("junie."):
                provider = model_id.split("/", 1)[0]
                host = _endpoint_host(
                    (provider_configs or {}).get(provider, {}).get("baseUrl")
                )
            resolved.append((model_id, host, role, source))
    return resolved


def audit_materialized(slim, junie_profiles=(), model_list=(), provider_configs=None):
    """Return metadata violations; this does not enforce request-time billing."""
    report = []
    for model_id, host, role, source in collect_routes(
        slim, junie_profiles, model_list, provider_configs
    ):
        result = classify_free_route(model_id, host, source, role)
        if result["classification"] != "free-classified":
            report.append(result)
    return sorted(
        report,
        key=lambda row: (row["route_source"], row["effective_role"], row["model_id"]),
    )


def _resolved_host(alias):
    if not isinstance(alias, dict):
        return None
    params = alias.get("litellm_params")
    if not isinstance(params, dict):
        return "unknown"
    if alias.get("model_name") == "opencode/big-pickle" and params.get("model") not in {
        "openai/big-pickle",
        "opencode/big-pickle",
        "big-pickle",
    }:
        return "unknown"
    return _endpoint_host(params.get("api_base"))


def _endpoint_host(api_base):
    if not isinstance(api_base, str):
        return "unknown"
    from urllib.parse import urlsplit

    def origin(value):
        parsed = urlsplit(value)
        port = parsed.port or {"https": 443, "http": 80}.get(parsed.scheme.lower())
        return parsed.scheme.lower(), (parsed.hostname or "").lower(), port

    try:
        target = origin(api_base)
        for provider, metadata in PROVIDER_ENDPOINTS.items():
            base = metadata.get("baseUrl")
            if base and origin(base) == target:
                return provider
    except ValueError:
        pass
    return "unknown"


def _free_junie_profiles(root, slim):
    """Resolve tracked free selections and twins with the production group helpers."""
    cfg = json.loads((root / "configs/junie/model-groups.json").read_text())
    spec = importlib.util.spec_from_file_location(
        "jetbrains_profiles", root / "scripts/generate-jetbrains-profiles.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load Junie profile generator")
    generator = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = generator
    spec.loader.exec_module(generator)
    groups = generator.resolve_model_groups(cfg, slim)
    names = {"free", "omo-slim-opencode-zen-free"}
    names.update(
        alias
        for alias, target in cfg.get("compatibility", {}).items()
        if target in names
    )
    selected = {name: group for name, group in groups.items() if name in names}
    if not selected:
        raise ValueError("No Junie free selections resolved")
    return [
        {
            "groups": generator.derive_litellm_groups(selected),
            "providers": cfg.get("providers", {}),
        }
    ]


def validate_presets(
    root=None, junie_profiles=(), model_list=(), provider_configs=None
):
    """Return unqualified emitted routes; unavailable provenance fails closed."""
    root = Path(root) if root else Path(__file__).resolve().parents[2]
    slim = json.loads((root / "configs/opencode/oh-my-opencode-slim.json").read_text())
    junie_path = root / "configs/junie/model-groups.json"
    profiles = list(junie_profiles)
    if junie_path.exists():
        profiles.extend(_free_junie_profiles(root, slim))
    return audit_materialized(slim, profiles, model_list, provider_configs)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Offline tracked-selection audit; unresolved deployment provenance fails closed (not billing enforcement).",
        allow_abbrev=False,
    )
    parser.parse_args(argv)
    report = validate_presets()
    for row in report:
        print(
            f"{row['classification']}: {row['route_source']} {row['effective_role']} {row['model_id']} ({row['upstream_host']})"
        )
    return 1 if report else 0


if __name__ == "__main__":
    raise SystemExit(main())
