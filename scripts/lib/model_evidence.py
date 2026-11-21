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

METRICS = frozenset({"reasoning", "coding", "agentic", "overall", "intelligence"})
# A BenchLM cache is one coherent four-surface generation, never a partial set.
BENCHLM_SOURCES = frozenset(
    {"benchlm-reasoning", "benchlm-coding", "benchlm-agentic", "benchlm-overall"}
)


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


def _normalize_source(payload, name, *, now):
    """Return one comparable source and metric, or reject its entire file."""
    metric = payload.get("metric") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or payload.get("source") != name
        or not isinstance(metric, str)
        or metric not in METRICS
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
        evidence = row.get("evidence")
        if not isinstance(evidence, str) or evidence not in {"independent", "mixed"}:
            continue
        sample_count = row.get("n")
        if sample_count is not None and (
            type(sample_count) is not int or sample_count < 3
        ):
            continue
        records[model_id] = {"creator_id": creator, "score": score}
    if not records:
        return None
    coverage = payload.get("coverage")
    metadata = {}
    if coverage is not None:
        if not isinstance(coverage, str) or coverage not in {
            "complete",
            "first-100-subset",
        }:
            return None
        count, population = payload.get("retrieved_rows"), payload.get(
            "population_total"
        )
        if (
            type(count) is not int
            or type(population) is not int
            or not 2 <= count <= population <= 2000
            or (coverage == "complete" and count != population)
            or (coverage == "first-100-subset" and count > 100)
        ):
            return None
        metadata = {
            "coverage": coverage,
            "retrieved_rows": count,
            "population_total": population,
        }
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
            or not all(
                isinstance(item, str) and SOURCE_ID.fullmatch(item) for item in members
            )
            or name not in members
        ):
            return None
    return {
        **({"bundle": bundle} if bundle is not None else {}),
        **metadata,
        "source": name,
        "metric": payload["metric"],
        "scale": payload["scale"],
        "observed_at": payload["observed_at"],
        "models": records,
    }


def _batch_complete(item, catalogues):
    """Reject interrupted multi-file updates without coupling to any provider."""
    bundle = item.get("bundle")
    if bundle is None:
        return True
    by_source = {entry["source"]: entry for entry in catalogues}
    return all(
        by_source.get(name, {}).get("bundle") == bundle for name in bundle["members"]
    )


def _benchlm_bundle_valid(rows, *, manifest_bundle=None):
    """One coherent four-surface generation for every consumer."""
    if len(rows) != len(BENCHLM_SOURCES):
        return False
    if {row["source"] for row in rows} != BENCHLM_SOURCES:
        return False
    bundle = rows[0].get("bundle")
    if not isinstance(bundle, dict) or set(bundle["members"]) != BENCHLM_SOURCES:
        return False
    if manifest_bundle is not None and bundle != manifest_bundle:
        return False
    coverage = rows[0].get("coverage")
    if coverage not in ("complete", "first-100-subset"):
        return False
    return all(
        row.get("bundle") == bundle
        and row.get("coverage") == coverage
        and row["metric"] == row["source"].removeprefix("benchlm-")
        and row["scale"].endswith("-top100") == (coverage == "first-100-subset")
        and len(row["models"]) <= row["retrieved_rows"]
        for row in rows
    )


def _manifest_sources(directory):
    """Return None for absent manifest, otherwise (paths, bundle) or ([], None)."""
    manifest_path = directory / "manifest.json"
    invalid = ([], None)
    if manifest_path.is_symlink():
        return invalid
    if not manifest_path.exists():
        return None
    if not manifest_path.is_file():
        return invalid
    try:
        if manifest_path.stat().st_size > 1024 * 1024:
            return invalid
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return invalid
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        return invalid
    generation = manifest.get("generation")
    sources = manifest.get("sources")
    bundle = manifest.get("bundle")
    expected = {name + ".json" for name in BENCHLM_SOURCES}
    if (
        not isinstance(generation, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", generation)
        or not isinstance(sources, list)
        or len(sources) != len(expected)
        or not all(isinstance(name, str) for name in sources)
        or set(sources) != expected
        or not isinstance(bundle, dict)
    ):
        return invalid
    generations = directory / "generations"
    root = generations / generation
    if generations.is_symlink() or root.is_symlink() or not root.is_dir():
        return invalid
    paths = [root / name for name in sources]
    if any(path.is_symlink() or not path.is_file() for path in paths):
        return invalid
    return paths, bundle


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
                    "observed_at": (aa.get("manifest") or {}).get("observed_at"),
                    "models": records,
                }
            )
    directory = Path(directory or sources_dir())
    if directory.is_symlink():
        return catalogues
    manifest = _manifest_sources(directory)
    paths = sorted(directory.glob("*.json"))
    manifest_bundle = None
    if manifest is not None:
        # A present (even corrupt) manifest supersedes flat BenchLM only.
        paths = [path for path in paths if not path.stem.startswith("benchlm-")]
        generation_paths, manifest_bundle = manifest
        paths.extend(generation_paths)
    for path in paths:
        if path.is_symlink() or not path.is_file():
            continue
        try:
            if path.stat().st_size > 8 * 1024 * 1024:
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        name = path.stem
        if name == "artificial-analysis":
            # Reserved for the built-in AA producer; a generic file must not
            # spoof its identity or scale.
            continue
        if not SOURCE_ID.fullmatch(name):
            continue
        normalized = _normalize_source(payload, name, now=now)
        if not normalized:
            continue
        catalogues.append(normalized)
    benchlm = [row for row in catalogues if row["source"].startswith("benchlm-")]
    if not _benchlm_bundle_valid(benchlm, manifest_bundle=manifest_bundle):
        catalogues = [
            row for row in catalogues if not row["source"].startswith("benchlm-")
        ]
    return [row for row in catalogues if _batch_complete(row, catalogues)]
