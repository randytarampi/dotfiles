"""Preset audit using the executable classifier owned by litellm_config."""

from __future__ import annotations

import json
from pathlib import Path

from litellm_config import classify_free_route


def _routes(node, role="unknown", source="preset"):
    found = []
    if isinstance(node, dict):
        model = node.get("model")
        provider = node.get("provider")
        if isinstance(model, str):
            if "/" in model:
                host, model_id = model.split("/", 1)[0], model
            elif provider:
                host, model_id = provider, f"{provider}/{model}"
            else:
                host, model_id = "unknown", model
            found.append((model_id, host, role, source))
        for key, value in node.items():
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
                found.append((value, value.split("/", 1)[0], role, source))
            else:
                found.extend(_routes(value, role, source))
    return found


def validate_presets(root=None):
    """Return structured unqualified routes in free and zen-free presets."""
    root = Path(root) if root else Path(__file__).resolve().parents[2]
    slim = json.loads((root / "configs/opencode/oh-my-opencode-slim.json").read_text())
    records = []
    for preset in ("free", "omo-slim-opencode-zen-free"):
        records.extend(_routes(slim.get("presets", {}).get(preset, {}), source=preset))
        tier = slim.get("_tiers", {}).get(preset, {})
        records.extend(_routes(tier.get("council", {}), source=f"{preset}.council"))
        records.extend(_routes(tier.get("fallback", {}), source=f"{preset}.fallback"))
    junie_path = root / "configs/junie/model-groups.json"
    if junie_path.exists():
        groups = json.loads(junie_path.read_text()).get("groups", {})
        if groups.get("free"):
            records.extend(_routes(groups["free"], source="junie.free"))
    report = []
    for model_id, host, role, source in records:
        result = classify_free_route(model_id, host, source, role)
        if result["classification"] != "free-classified":
            report.append(result)
    return sorted(
        report,
        key=lambda row: (row["route_source"], row["effective_role"], row["model_id"]),
    )
