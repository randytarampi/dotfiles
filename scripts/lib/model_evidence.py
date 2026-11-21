"""Offline model-quality evidence: exact source identities, never mixed scales.

AA Free supplies an Intelligence Index only. Additional independently sourced,
user-curated datasets share this small, versioned private JSON input contract.
No implicit network calls, inference capability claims, or API entitlement checks.
"""

import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from artificial_analysis import load_snapshot

AGE_LIMIT = timedelta(days=30)
SOURCE_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
ROLE_METRICS = {
    "reasoning": ("reasoning", "overall", "intelligence"),
    "code-gen": ("coding", "overall"),
    "lightweight": ("coding", "overall"),
    "solo": ("agentic", "overall"),
}
METRICS = frozenset({"reasoning", "coding", "agentic", "overall", "intelligence"})


def mapping_path():
    value = os.environ.get("DOTFILES_MODEL_EVIDENCE_MAP")
    if value:
        return Path(value).expanduser()
    return Path.home() / ".config" / "dotfiles" / "model-evidence-map.json"


def sources_dir():
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "dotfiles" / "model-evidence" / "sources"


def _identity(value):
    return (
        isinstance(value, str)
        and bool(value.strip())
        and not any(ord(char) < 32 for char in value)
    )


def _score(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def load_private_mappings(path=None):
    """Private v1: exact route -> source -> identity, creator, scoring scale."""
    path = Path(path or mapping_path())
    if path.is_symlink():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return {}
    if not isinstance(payload.get("mappings"), dict):
        return {}
    accepted = {}
    for route, source_refs in payload["mappings"].items():
        if (
            not isinstance(route, str)
            or not route.startswith(("ollama/", "omlx/"))
            or not route.split("/", 1)[1]
            or any(char in route for char in "*?[")
            or not isinstance(source_refs, dict)
        ):
            continue
        valid_sources = {}
        for source, identity in source_refs.items():
            if (
                isinstance(source, str)
                and SOURCE_ID.fullmatch(source)
                and isinstance(identity, dict)
                and all(
                    _identity(identity.get(k))
                    for k in ("model_id", "creator_id", "scale")
                )
            ):
                valid_sources[source] = identity
        if valid_sources:
            accepted[route] = valid_sources
    return accepted


def _normalize_source(payload, name, *, now):
    """Return one comparable source and metric, or reject its entire file."""
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or payload.get("source") != name
        or payload.get("metric") not in METRICS
        or not _identity(payload.get("scale"))
        or not isinstance(payload.get("observed_at"), str)
        or not isinstance(payload.get("models"), list)
    ):
        return None
    try:
        recorded = datetime.fromisoformat(payload["observed_at"].replace("Z", "+00:00"))
        if recorded.tzinfo is None:
            return None
        age = now - recorded
    except (ValueError, TypeError, OverflowError):
        return None
    if not timedelta(0) <= age <= AGE_LIMIT:
        return None
    records = {}
    for row in payload["models"]:
        if not isinstance(row, dict):
            return None
        model_id, creator = row.get("id"), row.get("creator_id")
        score = _score(row.get("score"))
        if not _identity(model_id) or not _identity(creator) or score is None:
            return None
        if model_id in records:
            return None
        if row.get("evidence") not in {"independent", "mixed"}:
            continue
        sample_count = row.get("n")
        if sample_count is not None and (
            type(sample_count) is not int or sample_count < 3
        ):
            continue
        records[model_id] = {"creator_id": creator, "score": score}
    if not records:
        return None
    bundle = payload.get("bundle")
    if bundle is not None:
        if not isinstance(bundle, dict):
            return None
        batch_id, members = bundle.get("id"), bundle.get("members")
        if (
            not _identity(batch_id)
            or not isinstance(members, list)
            or not 1 <= len(members) <= 16
            or len(set(map(str, members))) != len(members)
            or not all(isinstance(item, str) and SOURCE_ID.fullmatch(item) for item in members)
            or name not in members
        ):
            return None
    return {
        **({"bundle": bundle} if bundle is not None else {}),
        "source": name,
        "metric": payload["metric"],
        "scale": payload["scale"],
        "models": records,
    }


def _batch_complete(item, catalogues):
    """Reject interrupted multi-file updates without coupling to any provider."""
    bundle = item.get("bundle")
    if bundle is None:
        return True
    by_source = {entry["source"]: entry for entry in catalogues}
    return all(
        by_source.get(name, {}).get("bundle") == bundle
        for name in bundle["members"]
    )

def load_catalogues(directory=None, *, aa_evidence=None, now=None):
    """Read AA Intelligence and private v1 datasets without network access."""
    now = now or datetime.now(timezone.utc)
    catalogues = []
    aa = aa_evidence if aa_evidence is not None else load_snapshot(now=now)
    if aa:
        version = (aa.get("manifest") or {}).get("index_version")
        records = {}
        for row in aa.get("models", []):
            score = _score(row.get("intelligence"))
            if (
                score is not None
                and _identity(row.get("id"))
                and _identity(row.get("creator_id"))
            ):
                records[row["id"]] = {
                    "score": score,
                    "creator_id": row["creator_id"],
                }
        if version and records:
            catalogues.append(
                {
                    "source": "artificial-analysis",
                    "metric": "intelligence",
                    "scale": "aa-intelligence-" + str(version),
                    "models": records,
                }
            )
    directory = Path(directory or sources_dir())
    if directory.is_symlink():
        return catalogues
    for path in sorted(directory.glob("*.json")):
        if path.is_symlink():
            continue
        try:
            if path.stat().st_size > 8 * 1024 * 1024:
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        name = path.stem
        if not SOURCE_ID.fullmatch(name):
            continue
        normalized = _normalize_source(payload, name, now=now)
        if normalized:
            catalogues.append(normalized)
    return [row for row in catalogues if _batch_complete(row, catalogues)]


def _candidate_score(model, catalogue, mappings):
    # A local model must have actual known tool support to receive ranking.
    if "tools" not in (model.get("capabilities") or []):
        return None
    route = model.get("provider", "ollama") + "/" + model["name"]
    identity = mappings.get(route, {}).get(catalogue["source"])
    if not identity or identity["scale"] != catalogue["scale"]:
        return None
    entry = catalogue["models"].get(identity["model_id"])
    if not entry or entry["creator_id"] != identity["creator_id"]:
        return None
    return entry["score"]


def rank_qualified(classified, *, catalogues=None, mappings=None, profile=None):
    """Move only comparable slots within their category, never between scales."""
    if profile == "quiet":
        return 0
    catalogues = catalogues if catalogues is not None else load_catalogues()
    mappings = mappings if mappings is not None else load_private_mappings()
    if not catalogues or not mappings:
        return 0
    changed = 0
    for category, preferences in ROLE_METRICS.items():
        entries = classified.get(category)
        if not entries:
            continue
        selection = None
        for metric in preferences:
            possibilities = []
            for source in catalogues:
                if source["metric"] != metric:
                    continue
                scored = [
                    (index, _candidate_score(model, source, mappings))
                    for index, model in enumerate(entries)
                ]
                comparable = [(i, s) for i, s in scored if s is not None]
                if len(comparable) >= 2:
                    possibilities.append((source["source"], comparable))
            if possibilities:
                selection = sorted(
                    possibilities, key=lambda item: (-len(item[1]), item[0])
                )[0][1]
                break
        if selection is None:
            continue
        slots = [index for index, _ in selection]
        sorted_items = sorted(selection, key=lambda pair: pair[1], reverse=True)
        replacements = [entries[index] for index, _ in sorted_items]
        if any(entries[index] is not item for index, item in zip(slots, replacements)):
            changed += 1
        for index, item in zip(slots, replacements):
            entries[index] = item
    return changed
