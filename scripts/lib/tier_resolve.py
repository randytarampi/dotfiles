#!/usr/bin/env python3
"""Shared tier-resolution helpers for local Ollama model classification.

Extracted from configure-opencode-tier.py to eliminate dynamic module loading
across configure-opencode.py, configure-opencode-voice.py, and
configure-smallcode.py.

Provides:
  - extract_param_count(): parse :NNb/:NNm/:NNt size tags from model names
  - get_model_details(): run `ollama show <model>` and parse params/capabilities
  - resolve_roles_from_list(): classify a list of local models into role
    categories (reasoning, code-gen, lightweight, vision, solo) and return
    a {role: "ollama/model_name"} mapping.
"""

import re
import subprocess

import logger
from discover_models import find_ollama, list_local_ollama_models


def extract_param_count(model_name: str) -> int:
    """Extract parameter count from model name for size-based classification.

    Parses patterns like :27b, :480b, :1t from model tags.
    Returns 0 if no size pattern is found.
    """
    match = re.search(r":(\d+(?:\.\d+)?)([bmt])", model_name, re.IGNORECASE)
    if not match:
        return 0
    size = float(match.group(1))
    unit = match.group(2).lower()
    if unit == "t":
        return int(size * 1000)
    elif unit == "m":
        return max(1, int(size / 1000))
    else:
        return int(size)


def get_model_details(model_name: str) -> dict:
    """Run ollama show <model> and parse parameters/capabilities.

    Returns {"param_count": int, "capabilities": [str]}.
    Returns {"param_count": None, "capabilities": []} if ollama show fails or
    no parameters found.
    """
    ollama_bin = find_ollama()
    if not ollama_bin:
        return {"param_count": None, "capabilities": []}

    try:
        result = subprocess.run(
            [ollama_bin, "show", model_name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return {"param_count": None, "capabilities": []}

        param_count = None
        capabilities = []
        in_capabilities = False

        for line in result.stdout.splitlines():
            if not line.strip():
                in_capabilities = False
                continue

            if in_capabilities:
                if line[:1].isspace():
                    capability = line.strip()
                    if capability:
                        capabilities.append(capability)
                    continue
                in_capabilities = False

            if "parameters" in line:
                match = re.search(
                    r"parameters\s+([\d.]+)\s*([BMK])", line, re.IGNORECASE
                )
                if match:
                    value = float(match.group(1))
                    unit = match.group(2).upper()
                    if unit == "B":
                        param_count = int(value)
                    elif unit == "M":
                        param_count = int(value / 1000.0)
                    elif unit == "K":
                        param_count = int(value / 1000000.0)
                continue

            if line.strip().lower() == "capabilities":
                in_capabilities = True

        return {"param_count": param_count, "capabilities": capabilities}
    except Exception:
        return {"param_count": None, "capabilities": []}


def resolve_roles_from_list(models: list) -> dict:
    classified = {
        "reasoning": [],
        "code-gen": [],
        "_name_qualified_code_gen": [],
        "lightweight": [],
        "vision": [],
        "all": [],
    }
    model_details_cache = {}

    def get_cached_model_details(model_name: str) -> dict:
        if model_name not in model_details_cache:
            model_details_cache[model_name] = get_model_details(model_name)
        return model_details_cache[model_name]

    for model in models:
        if isinstance(model, dict):
            model_name = model.get("name", "")
            size_gb = float(model.get("size_gb", 0.0) or 0.0)
        else:
            model_name = str(model)
            size_gb = 0.0

        if not model_name:
            continue

        name_lower = model_name.lower()
        if any(
            p in name_lower
            for p in ["r1", "reasoning", "deep-think", "think", "qwq", "reflection"]
        ):
            category = "reasoning"
        elif any(
            p in name_lower
            for p in [
                "coder",
                "code",
                "coding",
                "devstral",
                "codestral",
                "laguna",
            ]
        ):
            category = "code-gen"
            classified["_name_qualified_code_gen"].append(
                {"name": model_name, "size_gb": size_gb}
            )
        elif any(p in name_lower for p in ["mini", "small", "tiny", "phi", "smol"]):
            category = "lightweight"
        elif size_gb < 12:
            category = "lightweight"
        else:
            category = "all"

        classified[category].append({"name": model_name, "size_gb": size_gb})

    remaining = classified["all"]
    if remaining:
        remaining_sorted = sorted(
            remaining,
            key=lambda m: extract_param_count(m["name"]),
            reverse=True,
        )
        large_models = []
        for model in remaining_sorted:
            details = get_cached_model_details(model["name"])
            param_count = details.get("param_count")
            if param_count is None:
                large_models.append(model)
                continue
            if param_count >= 7:
                classified["reasoning"].append(model)
            else:
                classified["code-gen"].append(model)

        if large_models:
            if len(large_models) >= 2:
                half = max(1, len(large_models) // 2)
                for m in large_models[:half]:
                    classified["reasoning"].append(m)
                for m in large_models[half:]:
                    classified["code-gen"].append(m)
            else:
                classified["reasoning"].extend(large_models)

    # Sort each category by descending size to prefer the most capable model
    def _effective_param_count(model):
        """Best-effort parameter count for sorting: name tag > ollama show > size heuristic."""
        name_count = extract_param_count(model.get("name", ""))
        if name_count > 0:
            return name_count
        return model.get("size_gb", 0.0)

    for cat in ["reasoning", "code-gen", "lightweight", "vision"]:
        # Enrich models without name-tag param counts via ollama show
        for model in classified[cat]:
            details = get_cached_model_details(model["name"])
            param_count = details.get("param_count")
            if param_count is not None:
                model["param_count"] = param_count
            model["capabilities"] = details.get("capabilities", [])

        def _sort_key(m):
            name_count = extract_param_count(m.get("name", ""))
            if name_count > 0:
                return name_count
            show_count = m.get("param_count")
            if show_count is not None and show_count > 0:
                return float(show_count)
            return m.get("size_gb", 0.0)

        classified[cat].sort(key=_sort_key, reverse=True)

    def _has_required_capabilities(model, required_caps):
        capabilities = model.get("capabilities", [])
        if not capabilities:
            return True
        return all(cap in capabilities for cap in required_caps)

    classified["reasoning"] = [
        m
        for m in classified["reasoning"]
        if _has_required_capabilities(m, ["thinking", "tools"])
    ]
    _code_gen_name_qualified = set()
    for m in classified.get("_name_qualified_code_gen", []):
        _code_gen_name_qualified.add(m["name"])
    classified["code-gen"] = [
        m
        for m in classified["code-gen"]
        if m["name"] in _code_gen_name_qualified
        or _has_required_capabilities(m, ["thinking", "completion"])
    ]
    classified["lightweight"] = [
        m for m in classified["lightweight"] if _has_required_capabilities(m, ["tools"])
    ]
    classified["vision"] = [
        m
        for m in classified["lightweight"]
        if _has_required_capabilities(m, ["vision"])
    ]
    classified["vision"].sort(key=lambda m: _effective_param_count(m), reverse=True)

    if not classified["vision"] and classified["lightweight"]:
        logger.warning(
            "No vision-capable local models found; falling back to lightweight"
        )
        classified["vision"] = classified["lightweight"][:]

    if not classified["code-gen"] and classified["reasoning"]:
        classified["code-gen"] = classified["reasoning"][:]

    # Build solo category: models with ALL 4 capabilities (completion + thinking + tools + vision).
    # Purely capability-based — no name heuristics. Sort by param count descending (prefer larger).
    required_solo_caps = {"completion", "thinking", "tools", "vision"}
    solo_candidates = []
    seen_names = set()
    for cat in ["reasoning", "code-gen", "lightweight", "vision"]:
        for model in classified[cat]:
            if model["name"] not in seen_names:
                seen_names.add(model["name"])
                caps = set(model.get("capabilities", []))
                if required_solo_caps.issubset(caps):
                    solo_candidates.append(model)
    classified["solo"] = sorted(
        solo_candidates, key=lambda m: _effective_param_count(m), reverse=True
    )

    if not classified["solo"]:
        logger.warning(
            "No solo models found (need completion+thinking+tools+vision); "
            "local-solo tier will fall back to code-gen+vision pattern"
        )

    def pick(category, fallback_category=None, index=0):
        """Pick the Nth model (0-based) from a category, with fallback."""
        src = classified.get(category, [])
        if index < len(src):
            return src[index]["name"]
        if fallback_category:
            src_fb = classified.get(fallback_category, [])
            if index < len(src_fb):
                return src_fb[index]["name"]
        # Final fallback: scan all categories for the Nth available model
        all_models = []
        for c in ["reasoning", "code-gen", "lightweight", "vision"]:
            all_models.extend(classified.get(c, []))
        if index < len(all_models):
            return all_models[index]["name"]
        return None

    role_map = {
        "reasoning": pick("reasoning", "code-gen"),
        "reasoning_2": pick("reasoning", "code-gen", index=1),
        "reasoning_3": pick("reasoning", "code-gen", index=2),
        "code-gen": pick("code-gen", "reasoning"),
        "code-gen_2": pick("code-gen", "reasoning", index=1),
        "lightweight": pick("lightweight", "code-gen"),
        "lightweight_2": pick("lightweight", "code-gen", index=1),
        "vision": pick("vision", "lightweight"),
        "solo": pick("solo", "code-gen"),
        "solo_2": pick("solo", "code-gen", index=1),
    }

    resolved = {}
    for role, model in role_map.items():
        if model:
            resolved[role] = "ollama/" + model

    return resolved
