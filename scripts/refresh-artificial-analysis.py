#!/usr/bin/env python3
"""Refresh or inspect a private Artificial Analysis Free API snapshot."""

import argparse
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))

from artificial_analysis import (  # noqa: E402
    EvidenceError,
    MAX_PAGES,
    cache_dir,
    fetch_free,
    load_snapshot,
    probe_free,
    write_snapshot,
)
from cli_helpers import add_common_args  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    add_common_args(parser)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--refresh", action="store_true", help="Fetch Free API evidence")
    group.add_argument("--offline", action="store_true", help="Inspect saved evidence")
    group.add_argument(
        "--probe", action="store_true", help="Validate one Free API page"
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=MAX_PAGES,
        help="Maximum pages during --refresh",
    )
    parser.add_argument("--cache-dir", type=Path, default=cache_dir())
    args = parser.parse_args()
    if args.max_pages != MAX_PAGES and not args.refresh:
        parser.error("--max-pages is only valid with --refresh")
    if args.dry_run:
        print("Artificial Analysis: dry-run; no network request or cache write")
        return 0
    if args.probe:
        try:
            probe_free(os.environ.get("ARTIFICIAL_ANALYSIS_API_KEY", ""))
        except (EvidenceError, OSError):
            print(
                "Artificial Analysis: first-page schema probe failed", file=sys.stderr
            )
            return 1
        print("Artificial Analysis: first-page schema verified; no cache written")
        return 0
    if args.refresh:
        try:
            csv_bytes, version, count = fetch_free(
                os.environ.get("ARTIFICIAL_ANALYSIS_API_KEY", ""),
                max_pages=args.max_pages,
            )
            write_snapshot(args.cache_dir, csv_bytes, version, count)
        except (EvidenceError, OSError):
            print(
                "Artificial Analysis refresh unavailable; prior snapshot preserved",
                file=sys.stderr,
            )
            return 1
        print("Artificial Analysis Free API snapshot verified and stored privately")
        return 0
    cached = load_snapshot(args.cache_dir)
    if cached is None:
        print("Artificial Analysis evidence unavailable; use capability/size fallback")
        return 1
    print("Artificial Analysis private snapshot verified (offline)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
