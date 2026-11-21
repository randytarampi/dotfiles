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
    offline_sources,
    store_snapshots,
)
from cli_helpers import add_common_args  # noqa: E402
from model_evidence import sources_dir  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    add_common_args(parser)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--refresh", action="store_true", help="Read Free current scores")
    actions.add_argument("--offline", action="store_true", help="Validate private cache")
    parser.add_argument("--cache-dir", type=Path, default=sources_dir())
    args = parser.parse_args()
    if args.dry_run:
        print("BenchLM Free: dry run; no network or writes")
        return 0
    try:
        if args.refresh:
            snapshots = fetch_snapshots()
            store_snapshots(snapshots, args.cache_dir)
        offline_sources(args.cache_dir)
    except (BenchlmError, OSError):
        print("BenchLM Free: unavailable; old evidence preserved", file=sys.stderr)
        return 1
    print("BenchLM Free: four private current rankings validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
