#!/usr/bin/env python3
"""Refresh or validate a private whatmodel.sh source-neutral quality snapshot."""

import argparse
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS / "lib"))

from cli_helpers import add_common_args  # noqa: E402
from model_evidence import load_catalogues, sources_dir  # noqa: E402
from whatmodel_evidence import (  # noqa: E402
    WhatmodelError,
    fetch_response,
    normalize_response,
    store_snapshot,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    add_common_args(parser)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--refresh", action="store_true", help="Fetch public evidence")
    actions.add_argument(
        "--offline", action="store_true", help="Validate private snapshot"
    )
    parser.add_argument("--cache-dir", type=Path, default=sources_dir())
    args = parser.parse_args()
    if args.dry_run:
        print("whatmodel evidence: dry-run (no writes or network)")
        return 0
    if args.refresh:
        try:
            snapshot = normalize_response(fetch_response())
            store_snapshot(snapshot, args.cache_dir)
        except (WhatmodelError, OSError):
            print(
                "whatmodel evidence unavailable; preserving old snapshot",
                file=sys.stderr,
            )
            return 1
        print("whatmodel public evidence stored privately and validated")
        return 0
    catalogues = load_catalogues(args.cache_dir, aa_evidence={})
    if not any(source["source"] == "whatmodel" for source in catalogues):
        print("whatmodel evidence unavailable offline", file=sys.stderr)
        return 1
    print("whatmodel private snapshot validated offline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
