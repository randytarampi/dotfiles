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


def mapping_path():
    value = os.environ.get("DOTFILES_MODEL_EVIDENCE_MAP")
    if value:
        return Path(value).expanduser()
    return Path.home() / ".config" / "dotfiles" / "model-evidence-map.json"


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


def _candidate_score(model, catalogue, mappings):
    # A local model must have actual known tool support to receive ranking.
    if "tools" not in (model.get("capabilities") or []):
        return None
    identity = mappings.get(_route(model), {}).get(catalogue["source"])
    if not isinstance(identity, dict):
        return None
    scale, model_id, creator = (
        identity.get("scale"),
        identity.get("model_id"),
        identity.get("creator_id"),
    )
    if not _identity(scale) or not _identity(model_id) or not _identity(creator):
        return None
    if scale != catalogue["scale"]:
        return None
    entry = catalogue["models"].get(model_id)
    if not entry or entry["creator_id"] != creator:
        return None
    return entry["score"]


def _route(model):
    return model.get("provider", "ollama") + "/" + model["name"]


def _audit_candidate_identity(model, source, mappings):
    ref = mappings.get(_route(model), {}).get(source["source"])
    if not isinstance(ref, dict):
        return "unmapped"
    scale, model_id, creator = (
        ref.get("scale"),
        ref.get("model_id"),
        ref.get("creator_id"),
    )
    if not _identity(scale) or not _identity(model_id) or not _identity(creator):
        return "missing_identifiers"
    if scale != source["scale"]:
        return "scale_mismatches"
    entry = source["models"].get(model_id)
    if entry is None or entry["creator_id"] != creator:
        return "missing_identifiers"
    return "matched"


def _scored_slots(entries, source, mappings):
    """Use one eligibility predicate for both the audit and comparison."""
    multiplicity = {}
    for model in entries:
        route = _route(model)
        multiplicity[route] = multiplicity.get(route, 0) + 1
    if source.get("coverage") == "first-100-subset":
        return []
    scored = []
    for index, model in enumerate(entries):
        if multiplicity[_route(model)] != 1:
            continue
        score = _candidate_score(model, source, mappings)
        if score is not None:
            scored.append((index, score))
    return scored


def _audit_source_coverage(classified, catalogues, mappings, audit):
    """Count unique qualified identities, per source and per real category."""
    qualified = {
        _route(model): model
        for cat in ROLE_METRICS
        for model in classified.get(cat, [])
        if "tools" in (model.get("capabilities") or [])
    }
    audit["eligible_unique"] = len(qualified)
    for source in catalogues:
        stats = {
            "qualified": len(qualified),
            "matched": 0,
            "unmapped": 0,
            "scale_mismatches": 0,
            "missing_identifiers": 0,
        }
        for model in qualified.values():
            result = _audit_candidate_identity(model, source, mappings)
            stats[result] += 1
        audit["coverage_by_source"][source["source"]] = stats
        audit["unmapped_candidates"] += stats["unmapped"]
        audit["scale_mismatches"] += stats["scale_mismatches"]
        audit["missing_identifiers"] += stats["missing_identifiers"]
        # Per-source totals collapse categories, but the denominator that
        # decides whether a comparison is possible is per category: ranking
        # needs two comparable candidates in one category.
        per_category = {}
        for category in ROLE_METRICS:
            entries = classified.get(category, [])
            unique = {_route(model): model for model in entries}
            tool_capable = {
                route: model
                for route, model in unique.items()
                if "tools" in (model.get("capabilities") or [])
            }
            category_stats = {
                "qualified": len(unique),
                "tool_capable": len(tool_capable),
                "matched": 0,
                "unmapped": 0,
                "scale_mismatches": 0,
                "missing_identifiers": 0,
                "duplicates": len(entries) - len(unique),
            }
            for model in tool_capable.values():
                category_stats[_audit_candidate_identity(model, source, mappings)] += 1
            eligible = (
                len(_scored_slots(entries, source, mappings))
                if source["metric"] in ROLE_METRICS[category]
                else 0
            )
            category_stats["eligible_unique"] = eligible
            # Potential pairs before quiet suppression; quiet still audits them.
            category_stats["eligible_pairs"] = eligible * (eligible - 1) // 2
            per_category[category] = category_stats
        audit["coverage_by_category"][source["source"]] = per_category


def _preferred_comparables(entries, preferences, catalogues, mappings, audit):
    """Compare distinct local routes only; repeated seats are never evidence."""
    multiplicity = {}
    for model in entries:
        route = _route(model)
        multiplicity[route] = multiplicity.get(route, 0) + 1
    if audit is not None:
        audit["duplicate_slots_skipped"] += sum(
            count for count in multiplicity.values() if count > 1
        )
    for metric in preferences:
        possibilities = []
        for source in catalogues:
            if source["metric"] != metric:
                continue
            # A first-N subset cannot establish that an absent model is worse,
            # so it stays observable but never orders candidates.
            if source.get("coverage") == "first-100-subset":
                if audit is not None:
                    audit["partial_sources_excluded"] += 1
                continue
            scored = _scored_slots(entries, source, mappings)
            if audit is not None:
                # Score occurrences, not pairs: a source can score each seat.
                audit["comparable"] += len(scored)
            if len(scored) >= 2:
                possibilities.append((source["source"], scored))
        if possibilities:
            chosen_source, selection = sorted(
                possibilities, key=lambda item: (-len(item[1]), item[0])
            )[0]
            return chosen_source, metric, selection
    return None


def _reorder_comparable_slots(entries, selection):
    slots = [index for index, _ in selection]
    sorted_items = sorted(selection, key=lambda pair: pair[1], reverse=True)
    replacements = [entries[index] for index, _ in sorted_items]
    changed = any(
        entries[index] is not item for index, item in zip(slots, replacements)
    )
    for index, item in zip(slots, replacements):
        entries[index] = item
    return int(changed)


def rank_qualified(
    classified, *, catalogues=None, mappings=None, profile=None, audit=None
):
    """Move only comparable category slots within a versioned source scale."""
    if audit is not None:
        audit.update(
            {
                "sources": 0,
                "mappings": 0,
                "comparable": 0,
                "scale_mismatches": 0,
                "missing_identifiers": 0,
                "unmapped_candidates": 0,
                "eligible_unique": 0,
                "duplicate_slots_skipped": 0,
                "coverage_by_source": {},
                "coverage_by_category": {},
                "partial_sources_excluded": 0,
                "selected": {},
                "outcome": "no-qualified-pair",
            }
        )
    catalogues = catalogues if catalogues is not None else load_catalogues()
    mappings = mappings if mappings is not None else load_private_mappings()
    if audit is not None:
        audit["sources"] = len(catalogues)
        audit["mappings"] = len(mappings)
        # Audit even when quiet or unmapped: a missing proposal is a fact to
        # report, not a reason to leave the counts empty.
        if catalogues:
            _audit_source_coverage(classified, catalogues, mappings, audit)
    if profile == "quiet":
        if audit is not None:
            audit["outcome"] = "quiet-suppressed"
        return 0
    if not catalogues or not mappings:
        return 0
    changed = 0
    compared = False
    for category, preferences in ROLE_METRICS.items():
        entries = classified.get(category)
        if not entries:
            continue
        choice = _preferred_comparables(
            entries, preferences, catalogues, mappings, audit
        )
        if choice is None:
            continue
        compared = True
        source_id, metric, selection = choice
        if audit is not None:
            chosen = next(
                (source for source in catalogues if source["source"] == source_id),
                {},
            )
            audit["selected"][category] = {
                "source": source_id,
                "metric": metric,
                "candidates": len(selection),
                "eligible_pairs": len(selection) * (len(selection) - 1) // 2,
                "coverage": chosen.get("coverage", "unreported"),
                "retrieved_rows": chosen.get("retrieved_rows"),
                "population_total": chosen.get("population_total"),
            }
        changed += _reorder_comparable_slots(entries, selection)
    if audit is not None and compared:
        audit["outcome"] = "proposed-reorder" if changed else "same-order"
    return changed
