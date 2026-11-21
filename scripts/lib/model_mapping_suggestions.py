"""Private, read-only model-evidence mapping suggestions and explicit acceptance.

Never infer benchmark-to-quantized checkpoint equivalence from name similarity.
Only an owner's exact route, source and checkpoint approval can enter a map.
"""

import json
import os
import re
import stat
from pathlib import Path

from ai_models import is_specialized_model_name
from model_evidence import load_private_mappings, mapping_path

RUNTIME_SUFFIXES = frozenset(
    {"mlx", "gguf", "ollama", "bf16", "fp16", "fp32", "mxfp8", "nvfp4"}
)
DISTINGUISHING = frozenset(
    {
        "mini",
        "small",
        "medium",
        "large",
        "coder",
        "instruct",
        "thinking",
        "vl",
        "vision",
    }
)


class MappingSuggestionError(ValueError):
    """Untrusted source or unapproved identity; do not modify user state."""


def _tokens(label):
    # Model slugs can include provider namespaces and runtime quantization tags.
    name = label.split("/")[-1].lower()
    # Split runtime prefixes and quantisation markers for simpler auditing.
    name = re.sub(
        r"(?:^|[-_:])(?:mlx|gguf|ollama|mxfp\d+|nvfp4|bf16|fp16|fp32)(?=$|[-_:])",
        "-",
        name,
    )
    name = re.sub(
        r"(?:^|[-_:])(?:q[2-8](?:_[a-z0-9]+)?|\d+bit)(?=$|[-_:])",
        "-",
        name,
    )
    return tuple(
        part
        for part in re.findall(
            r"[a-z]+\d+(?:\.\d+)?[abtm]?|[a-z]+|\d+(?:\.\d+)?[abtm]?",
            name,
        )
        if part not in RUNTIME_SUFFIXES
    )


def _similarity(local, upstream):
    first, second = set(_tokens(local)), set(_tokens(upstream))
    if not first or not second:
        return 0.0
    if next(iter(_tokens(local)), None) != next(iter(_tokens(upstream)), None):
        return 0.0
    if (first ^ second) & DISTINGUISHING:
        return 0.0
    local_sizes = {x for x in first if re.fullmatch(r"\d+(?:\.\d+)?[bt]", x)}
    upstream_sizes = {x for x in second if re.fullmatch(r"\d+(?:\.\d+)?[bt]", x)}
    if local_sizes and upstream_sizes and local_sizes != upstream_sizes:
        return 0.0
    shared = len(first & second)
    if shared < 2:
        return 0.0
    score = shared / len(first | second)
    return score if score >= 0.7 else 0.0


def qualified_local_models(records, details=None):
    """Use local metadata; do not claim unknown capability or map specialized tools."""
    qualified, seen = [], set()
    for item in records:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        name = item["name"]
        provider = item.get("provider", "ollama")
        if (
            provider not in {"ollama", "omlx"}
            or not name
            or "/" in name
            and name.endswith("/")
        ):
            continue
        route = provider + "/" + name
        if route in seen:
            continue
        seen.add(route)
        if is_specialized_model_name(name) or item.get("model_type") not in (
            None,
            "llm",
            "vlm",
        ):
            continue
        metadata = details(name, provider) if details else item
        if not isinstance(metadata, dict):
            continue
        if metadata.get("model_type") not in (None, "llm", "vlm"):
            continue
        caps = set(item.get("capabilities") or []) | set(
            metadata.get("capabilities") or []
        )
        if not {"completion", "tools"}.issubset(caps):
            continue
        qualified.append(
            {
                "route": route,
                "capabilities": sorted(caps),
                "quantization": metadata.get("quantization")
                or item.get("quantization"),
            }
        )
    return sorted(qualified, key=lambda m: m["route"])


def _provenance(source):
    name = source["source"]
    if name == "artificial-analysis":
        link = "https://artificialanalysis.ai/data-api"
    elif name.startswith("benchlm-"):
        link = "https://benchlm.ai/data/access"
    else:
        link = "source-specific private cache"
    observed = source.get("observed_at") or "UNKNOWN"
    coverage = source.get("coverage", "unreported")
    return (
        f"Source: {link}; as-of: {observed}; coverage: {coverage}; "
        "checkpoint equivalence: UNVERIFIED"
    )


def suggestions_for(models, catalogues, aa_snapshot=None, *, maximum=3):
    """Return candidates for human inspection, with no score or key material."""
    if type(maximum) is not int or not 1 <= maximum <= 10:
        raise MappingSuggestionError("invalid candidate limit")
    names = {
        row["id"]: (row.get("slug") or row["id"], row.get("name") or row["id"])
        for row in (aa_snapshot or {}).get("models", [])
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    report = []
    for local in models:
        matched = []
        for source in catalogues:
            for model_id, evidence in source["models"].items():
                labels = (
                    names.get(model_id, (model_id,))
                    if source["source"] == "artificial-analysis"
                    else (model_id,)
                )
                best = max(
                    (_similarity(local["route"], label) for label in labels),
                    default=0.0,
                )
                if best <= 0:
                    continue
                matched.append(
                    {
                        "source": source["source"],
                        "metric": source["metric"],
                        "model_id": model_id,
                        "creator_id": evidence["creator_id"],
                        "scale": source["scale"],
                        "match": (
                            "identical-normalized-tokens"
                            if best >= 0.999
                            else "lexical-only"
                        ),
                        "score": best,
                        "provenance": _provenance(source),
                    }
                )
        matched.sort(
            key=lambda item: (-item["score"], item["source"], item["model_id"])
        )
        report.append(
            {
                **local,
                "candidates": [
                    {k: v for k, v in c.items() if k != "score"}
                    for c in matched[:maximum]
                ],
                "caveat": (
                    "Name similarity never proves checkpoint, runtime or "
                    "quantization equivalence; verify the original model card."
                ),
            }
        )
    return report


def approved_patch(approvals, models, catalogues):
    """Accept only exact owner-supplied route=source:model-id references."""
    by_route = {item["route"] for item in models}
    by_source = {item["source"]: item for item in catalogues}
    approved = {}
    for token in approvals:
        if "=" not in token or ":" not in token.split("=", 1)[1]:
            raise MappingSuggestionError("approval must be route=source:model-id")
        route, requested = token.split("=", 1)
        source, model_id = requested.split(":", 1)
        if route not in by_route or source not in by_source:
            raise MappingSuggestionError("route or source is not qualified and cached")
        catalogue = by_source[source]
        record = catalogue["models"].get(model_id)
        if record is None:
            raise MappingSuggestionError(
                "model id not found in eligible private source"
            )
        ref = {
            "model_id": model_id,
            "creator_id": record["creator_id"],
            "scale": catalogue["scale"],
        }
        if source in approved.get(route, {}) and approved[route][source] != ref:
            raise MappingSuggestionError("conflicting approvals")
        approved.setdefault(route, {})[source] = ref
    if not approved:
        raise MappingSuggestionError("no approvals supplied")
    return approved


def preview_merge(approved, *, path=None):
    """Preview the merged map without writing; owner data stays read-only."""
    if not hasattr(os, "getuid"):
        raise MappingSuggestionError(
            "private mapping preview requires POSIX ownership checks"
        )
    path = Path(path or mapping_path()).expanduser()
    if path.is_symlink() or path.parent.is_symlink():
        raise MappingSuggestionError("refusing symlink mapping path")
    if path.exists():
        state = path.stat()
        if (
            not stat.S_ISREG(state.st_mode)
            or stat.S_IMODE(state.st_mode) != 0o600
            or state.st_uid != os.getuid()
        ):
            raise MappingSuggestionError(
                "existing map must be owned private regular file"
            )
        try:
            original = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise MappingSuggestionError(
                "existing map is unreadable or malformed"
            ) from None
        if (
            not isinstance(original, dict)
            or original.get("schema_version") != 1
            or not isinstance(original.get("mappings"), dict)
            or original["mappings"] != load_private_mappings(path)
        ):
            raise MappingSuggestionError(
                "existing map contains unknown or invalid entries"
            )
    else:
        original = {"schema_version": 1, "mappings": {}}
    result = json.loads(json.dumps(original))
    for route, by_source in approved.items():
        if not isinstance(by_source, dict):
            raise MappingSuggestionError("invalid approved mapping")
        target = result["mappings"].setdefault(route, {})
        for source, item in by_source.items():
            if source in target and target[source] != item:
                raise MappingSuggestionError(
                    "existing mapping conflict; review it manually"
                )
            target[source] = item
    return result
