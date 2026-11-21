"""Read-only, offline suggestions for exact private model evidence mappings.

A name resemblance is not checkpoint identity, quantisation equivalence, or
permission to use benchmark evidence. No network, configuration writes or
automatic mapping approvals occur in this module.
"""

import json
import re
from collections import Counter
from pathlib import Path

from model_evidence import ROLE_METRICS


def _route(model):
    return model.get("provider", "ollama") + "/" + model["name"]


def _name_key(value):
    """Weak name-only comparison, for a REVIEW suggestion, never admission."""
    text = value.casefold().rsplit("/", 1)[-1]
    for token in ("mxfp8", "gguf", "mlx", "q4_k_m", "q5_k_m", "4bit", "6bit", "8bit"):
        text = re.sub(r"(?<![a-z0-9])" + token + r"(?![a-z0-9])", "", text)
    return re.sub(r"[^a-z0-9]", "", text)


def _param_token(value):
    result = re.search(
        r"(?<![a-z0-9])(\d+(?:\.\d+)?[bm])(?![a-z0-9])", value.casefold()
    )
    return result.group(1) if result else None


def _display_aliases(entry):
    aliases = []
    for key in ("id", "slug", "name", "display_name"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            aliases.extend((value, value.split(":", 1)[-1]))
    return aliases


def _private_metadata(source, directory):
    """Read display-only metadata from a source already validated by the ranker."""
    if source["source"] == "artificial-analysis":
        return {}, "AA Free Intelligence Index; private identity metadata"
    path = Path(directory) / (source["source"] + ".json")
    if path.is_symlink():
        return {}, "unavailable; untrusted symlink"
    try:
        if path.stat().st_size > 8 * 1024 * 1024:
            return {}, "unavailable; oversized data"
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, "unavailable; no private source metadata"
    if not isinstance(payload, dict) or payload.get("source") != source["source"]:
        return {}, "unavailable; mismatched source metadata"
    raw = payload.get("models")
    metadata = {}
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            ident = item.get("id")
            if not isinstance(ident, str):
                continue
            canonical = source["models"].get(ident)
            if not canonical or item.get("creator_id") != canonical["creator_id"]:
                continue
            metadata[ident] = {
                field: item[field]
                for field in ("name", "slug", "display_name")
                if isinstance(item.get(field), str)
            }
    coverage = payload.get("coverage")
    if isinstance(coverage, dict):
        mode = str(coverage.get("mode", "unknown"))[:30]
        count = coverage.get("retrieved")
        total = coverage.get("total")
        notice = (
            f"coverage {mode}: {count}/{total} ranked rows"
            if type(count) is int and type(total) is int
            else "coverage unknown"
        )
    elif isinstance(coverage, str):
        notice = "reviewed subset; completeness not established"
    else:
        notice = "coverage unknown; no completeness claim"
    return metadata, notice


def build_suggestions(
    classified, catalogues, aa_snapshot=None, directory=None, mappings=None
):
    """Return private, score-free owner-review proposals; never a final mapping."""
    mappings = mappings or {}
    aa_snapshot = aa_snapshot or {}
    directory = Path(directory) if directory else Path("/dev/null")
    aa_descriptors = {}
    for entry in aa_snapshot.get("models", []):
        if not isinstance(entry, dict):
            continue
        aa_descriptors[entry.get("id")] = {
            "id": entry.get("id"),
            "slug": entry.get("slug"),
            "name": entry.get("name"),
        }
    sources = []
    records = {}
    for source in catalogues:
        descriptions, coverage = _private_metadata(source, directory)
        if source["source"] == "artificial-analysis":
            descriptions = aa_descriptors
        sources.append(
            {
                "source": source["source"],
                "metric": source["metric"],
                "scale": source["scale"],
                "coverage": coverage,
                "eligible_upstream_identities": len(source["models"]),
            }
        )
        for ident, item in source["models"].items():
            details = {"id": ident, **descriptions.get(ident, {})}
            records[(source["source"], ident)] = {
                "creator_id": item["creator_id"],
                "aliases": _display_aliases(details),
            }
    local = {}
    excluded_no_tools = set()
    for category in ROLE_METRICS:
        for model in classified.get(category, []):
            route = _route(model)
            # Use exactly the ranker's hard eligibility gate, not just the
            # broader category's preliminary capability requirements.
            if "tools" not in (model.get("capabilities") or []):
                excluded_no_tools.add(route)
                continue
            if route not in local:
                local[route] = {"categories": [], "model": model}
            if category not in local[route]["categories"]:
                local[route]["categories"].append(category)
    proposals = []
    for route, record in sorted(local.items()):
        model = record["model"]
        model_name = model["name"]
        local_key = _name_key(model_name)
        local_param = _param_token(model_name)
        candidates = []
        eligible_metrics = {
            metric
            for category in record["categories"]
            for metric in ROLE_METRICS[category]
        }
        for source in catalogues:
            if source["metric"] not in eligible_metrics:
                continue
            for ident, entry in sorted(source["models"].items()):
                record_key = (source["source"], ident)
                upstream = records[record_key]
                same_name = local_key and any(
                    local_key == _name_key(alias) for alias in upstream["aliases"]
                )
                if not same_name:
                    continue
                if local_param is not None:
                    upstream_params = {
                        _param_token(alias) for alias in upstream["aliases"]
                    } - {None}
                    if upstream_params and local_param not in upstream_params:
                        continue
                existing = mappings.get(route, {}).get(source["source"])
                already = (
                    existing is not None
                    and existing.get("model_id") == ident
                    and existing.get("creator_id") == upstream["creator_id"]
                    and existing.get("scale") == source["scale"]
                )
                candidates.append(
                    {
                        "source": source["source"],
                        "metric": source["metric"],
                        "candidate": {
                            "model_id": ident,
                            "creator_id": upstream["creator_id"],
                            "scale": source["scale"],
                        },
                        "suggestion_basis": "name resemblance only; NOT verified checkpoint equivalence",
                        "owner_verified": False,
                        "already_mapped": already,
                        "coverage": next(
                            s["coverage"]
                            for s in sources
                            if s["source"] == source["source"]
                        ),
                        "provenance_note": (
                            source["source"] + " / " + source["metric"]
                            + " / " + source["scale"]
                            + " — upstream score is NOT an observed local "
                            "quantised model result"
                        ),
                    }
                )
        proposals.append(
            {
                "local_route": route,
                "categories": record["categories"],
                "quantization": model.get("quantization") or "unknown",
                "candidates": candidates,
                "mapping_review": (
                    "Check upstream checkpoint, revision, creator, architecture, "
                    "parameter count, fine-tune, quantisation and provenance. "
                    "Never infer equivalence from a name match."
                ),
            }
        )
    counts = Counter()
    for entry in proposals:
        for suggestion in entry["candidates"]:
            counts[suggestion["source"]] += 1
    return {
        "mode": "read-only-suggestions",
        "benchmark_api_requests": 0,
        "writes": 0,
        "automatic_approvals": 0,
        "qualified_tool_capable_unique_routes": len(proposals),
        "excluded_without_tool_capability": len(excluded_no_tools),
        "sources": sources,
        "potential_matches_by_source": dict(sorted(counts.items())),
        "models": proposals,
        "warning": (
            "Unmapped or non-matching upstream identities remain unranked. "
            "These candidates are NOT a trusted model-evidence-map.json."
        ),
    }
