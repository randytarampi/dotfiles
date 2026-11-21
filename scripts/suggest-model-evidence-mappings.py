#!/usr/bin/env python3
"""Suggest private exact model-evidence mappings from the installed local fleet."""

import argparse
import json
import os
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS / "lib"))

from artificial_analysis import cache_dir, load_snapshot  # noqa: E402
from cli_helpers import add_common_args  # noqa: E402
from local_engines import (  # noqa: E402
    engine_gate_active,
    iter_engine_discovery,
    merge_local_pools,
)
from model_evidence import load_catalogues, mapping_path, sources_dir  # noqa: E402
from model_mapping_suggestions import (  # noqa: E402
    MappingSuggestionError,
    approved_patch,
    preview_merge,
    qualified_local_models,
    suggestions_for,
)
from tier_resolve import resolve_roles_from_list  # noqa: E402


def _records(path):
    """Testing and offline/replay mode; never scan services with --models-file."""
    path = Path(path).expanduser()
    if path.is_symlink() or path.stat().st_size > 1024 * 1024:
        raise MappingSuggestionError("refusing unsafe model inventory file")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise MappingSuggestionError("model inventory must be a JSON list")
    return data


def _installed():
    """Capture typed discovery once per active engine, then merge the pools.

    Reuses the production merge so the observed pool and the tier generator's
    pool cannot diverge, including the ``equivalent_ollama_names`` union.
    """
    captured = {}
    for provider in ("ollama", "omlx"):
        if not engine_gate_active(provider):
            continue
        result = iter_engine_discovery(provider)
        if result.status not in {"ok_models", "ok_empty"}:
            raise MappingSuggestionError(
                "local discovery incomplete for " + provider + "; no suggestions"
            )
        captured[provider] = [{**row, "provider": provider} for row in result.records]
    return merge_local_pools(
        captured.get("ollama", []),
        [
            (provider, rows)
            for provider, rows in captured.items()
            if provider != "ollama"
        ],
    )


def _observed_categories(records, profile=None, min_reasoning_embedding=0):
    """Return the real resolver's post-qualification records, not a re-probe.

    Read-only: the resolver deep-copies its candidate categories for the
    observer, so qualification, the profile policy and the reasoning embedding
    floor are exactly the ones the tier generator would apply. The observed
    model dicts are then the source for suggestions, so this makes no second
    metadata lookup.
    """
    observed = {}

    def collect(categories):
        for category, models in categories.items():
            for model in models:
                route = model.get("provider", "ollama") + "/" + model["name"]
                entry = observed.setdefault(
                    route, {"model": model, "categories": set()}
                )
                entry["categories"].add(category)

    resolve_roles_from_list(
        records,
        profile=profile,
        min_reasoning_embedding=min_reasoning_embedding,
        category_observer=collect,
    )
    return {
        route: {"model": entry["model"], "categories": sorted(entry["categories"])}
        for route, entry in observed.items()
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    add_common_args(parser)
    parser.add_argument(
        "--models-file", type=Path, help="Offline fixture; no local model probe"
    )
    parser.add_argument("--cache-dir", type=Path, default=sources_dir())
    parser.add_argument("--aa-cache-dir", type=Path, default=cache_dir())
    parser.add_argument("--map", type=Path, default=mapping_path())
    parser.add_argument("--max-candidates", type=int, default=3)
    parser.add_argument(
        "--profile",
        choices=("quiet", "balanced", "loud"),
        default=None,
        help="Ask the resolver for categories under this local profile policy",
    )
    parser.add_argument(
        "--min-reasoning-embedding",
        type=int,
        default=None,
        help="Match the tier generator's reasoning embedding floor "
        "(default: DOTFILES_MIN_REASONING_EMBEDDING or 0)",
    )
    parser.add_argument(
        "--json", action="store_true", help="Print a machine-readable private report"
    )
    parser.add_argument(
        "--accept",
        action="append",
        default=[],
        metavar="ROUTE=SOURCE:MODEL_ID",
        help="Explicitly approve an exact existing source row; repeatable",
    )
    args = parser.parse_args()
    if args.dry_run:
        print("mapping onboarding: dry run; no API requests or file writes")
        return 0
    if args.min_reasoning_embedding is None:
        try:
            args.min_reasoning_embedding = int(
                os.environ.get("DOTFILES_MIN_REASONING_EMBEDDING", "0") or "0"
            )
        except ValueError as error:
            parser.error(f"Invalid DOTFILES_MIN_REASONING_EMBEDDING value: {error}")
    try:
        # A fixture may not probe services; only live discovery gets the real
        # post-qualification category observation and a single merged pool.
        if args.models_file:
            records = _records(args.models_file)
            local = qualified_local_models(records, details=None)
            observed = {}
        else:
            records = _installed()
            observed = _observed_categories(
                records, args.profile, args.min_reasoning_embedding
            )
            if not observed:
                raise MappingSuggestionError(
                    "no post-qualification local candidates; "
                    "refusing unfiltered suggestions"
                )
            # Suggestions come from the resolver's own records, not a second
            # metadata lookup.
            local = sorted(
                (
                    {
                        "route": route,
                        "capabilities": sorted(
                            set(entry["model"].get("capabilities") or [])
                        ),
                        "quantization": entry["model"].get("quantization"),
                    }
                    for route, entry in observed.items()
                ),
                key=lambda model: model["route"],
            )
        aa = load_snapshot(args.aa_cache_dir)
        sources = load_catalogues(args.cache_dir, aa_evidence=aa or {})
        if args.accept:
            result = preview_merge(
                approved_patch(args.accept, local, sources),
                path=args.map,
            )
            print(json.dumps(result, sort_keys=True, indent=2))
            print("PREVIEW ONLY: no private map was written", file=sys.stderr)
            return 0
        report = suggestions_for(
            local, sources, aa_snapshot=aa, maximum=args.max_candidates
        )
        for row in report:
            row["categories"] = observed.get(row["route"], {}).get("categories", [])
        if args.json:
            print(json.dumps(report, sort_keys=True, indent=2))
        else:
            print("Private model mapping suggestions (not approved; no scores)")
            print(
                "Effective profile:",
                args.profile or "resolver default (no profile policy)",
            )
            print("Min reasoning embedding:", args.min_reasoning_embedding)
            print("Qualified installed candidates:", len(report))
            print("Validated offline sources:", len(sources))
            for row in report:
                print(row["route"], "quantization=", row["quantization"] or "UNKNOWN")
                print(
                    "  categories:",
                    ",".join(row["categories"]) or "UNOBSERVED",
                )
                if not row["candidates"]:
                    print(
                        "  No plausible exact upstream identity in available snapshots"
                    )
                for choice in row["candidates"]:
                    print(
                        "  "
                        + choice["source"]
                        + ": "
                        + choice["model_id"]
                        + " (creator="
                        + choice["creator_id"]
                        + ", scale="
                        + choice["scale"]
                        + ", "
                        + choice["match"]
                        + ")"
                    )
                    print("    " + choice["provenance"])
                print("  " + row["caveat"])
            print(
                "No changes made; use --accept for a JSON preview; this tool never writes."
            )
        return 0
    except (OSError, ValueError) as error:
        print("mapping onboarding unavailable: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
