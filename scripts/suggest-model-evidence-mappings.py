#!/usr/bin/env python3
"""Suggest private exact model-evidence mappings from the installed local fleet."""

import argparse
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS / "lib"))

from artificial_analysis import cache_dir, load_snapshot  # noqa: E402
from cli_helpers import add_common_args  # noqa: E402
from local_engines import engine_gate_active, iter_engine_discovery  # noqa: E402
from model_evidence import load_catalogues, mapping_path, sources_dir  # noqa: E402
from model_mapping_suggestions import (  # noqa: E402
    MappingSuggestionError,
    approved_patch,
    merge_approved,
    qualified_local_models,
    suggestions_for,
)
from tier_resolve import get_model_details  # noqa: E402


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
    records = []
    for provider in ("ollama", "omlx"):
        if not engine_gate_active(provider):
            continue
        result = iter_engine_discovery(provider)
        if result.status not in {"ok_models", "ok_empty"}:
            raise MappingSuggestionError(
                "local discovery incomplete for " + provider + "; no suggestions"
            )
        records.extend({**row, "provider": provider} for row in result.records)
    return records


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
        "--json", action="store_true", help="Print a machine-readable private report"
    )
    parser.add_argument(
        "--accept",
        action="append",
        default=[],
        metavar="ROUTE=SOURCE:MODEL_ID",
        help="Explicitly approve an exact existing source row; repeatable",
    )
    parser.add_argument(
        "--apply", action="store_true", help="Write approved identities to private map"
    )
    args = parser.parse_args()
    if args.apply and not args.accept:
        parser.error("--apply requires one or more --accept approvals")
    if args.apply and args.models_file:
        parser.error("--apply requires live installed-model discovery, not a fixture")
    if args.dry_run:
        print("mapping onboarding: dry run; no API requests or file writes")
        return 0
    try:
        records = _records(args.models_file) if args.models_file else _installed()
        local = qualified_local_models(
            records, details=None if args.models_file else get_model_details
        )
        aa = load_snapshot(args.aa_cache_dir)
        sources = load_catalogues(args.cache_dir, aa_evidence=aa or {})
        if args.accept:
            result = merge_approved(
                approved_patch(args.accept, local, sources),
                path=args.map,
                apply=args.apply,
            )
            print(json.dumps(result, sort_keys=True, indent=2))
            if not args.apply:
                print("PREVIEW ONLY: no private map was written", file=sys.stderr)
            return 0
        report = suggestions_for(
            local, sources, aa_snapshot=aa, maximum=args.max_candidates
        )
        if args.json:
            print(json.dumps(report, sort_keys=True, indent=2))
        else:
            print("Private model mapping suggestions (not approved; no scores)")
            print("Qualified installed candidates:", len(report))
            print("Validated offline sources:", len(sources))
            for row in report:
                print(row["route"], "quantization=", row["quantization"] or "UNKNOWN")
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
                print("  " + row["caveat"])
            print(
                "No changes made; use --accept for a JSON preview, --apply only after owner review."
            )
        return 0
    except (MappingSuggestionError, OSError, ValueError) as error:
        print("mapping onboarding unavailable: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
