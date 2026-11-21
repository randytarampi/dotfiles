#!/usr/bin/env python3
"""Refresh or validate private BenchLM Free current-ranking evidence."""

import argparse
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS / "lib"))

from benchlm_evidence import (  # noqa: E402
    BenchlmError,
    fetch_snapshots,
    probe_surface,
    offline_sources,
    store_snapshots,
)
from cli_helpers import add_common_args  # noqa: E402
from model_evidence import sources_dir  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    add_common_args(parser)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--refresh", action="store_true", help="Read Free current scores"
    )
    actions.add_argument(
        "--offline", action="store_true", help="Validate private cache"
    )
    actions.add_argument("--probe", action="store_true", help="One-page schema test")
    parser.add_argument(
        "--surface",
        choices=("reasoning", "coding", "agentic", "overall"),
        help="Select surface for --probe (defaults to coding)",
    )
    parser.add_argument(
        "--max-requests",
        type=int,
        default=12,
        help="Hard total HTTP request budget for full refresh (default 12)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=20,
        help="Hard per-surface page cap for full refresh (default 20)",
    )
    parser.add_argument(
        "--coverage",
        choices=("complete", "first-100"),
        default="complete",
        help="Require full Free source or accept explicit incomplete first-100 subset",
    )
    parser.add_argument("--cache-dir", type=Path, default=sources_dir())
    args = parser.parse_args()
    if args.surface and not args.probe:
        parser.error("--surface is only valid with --probe")
    if args.dry_run:
        print("BenchLM Free: dry run; no network or writes")
        return 0
    try:
        if args.probe:
            result = probe_surface(surface=args.surface or "coding")
            print(
                "BenchLM Free: first-page schema valid for "
                + result["surface"]
                + "; no cache written"
            )
            return 0
        if args.refresh:
            snapshots = fetch_snapshots(
                max_requests=args.max_requests,
                max_pages=args.max_pages,
                coverage_mode=args.coverage,
            )
            store_snapshots(snapshots, args.cache_dir)
        offline_sources(args.cache_dir)
    except BenchlmError as error:
        print(
            "BenchLM Free: "
            + str(error)
            + "; old evidence left unchanged when fetch failed",
            file=sys.stderr,
        )
        return 1
    except OSError:
        print(
            "BenchLM Free: unavailable; old evidence may require inspection",
            file=sys.stderr,
        )
        return 1
    print(
        "BenchLM Free: four private current rankings validated "
        + "(first-100 subset; incomplete coverage)"
        if args.coverage == "first-100"
        else "BenchLM Free: four private complete rankings validated"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
