#!/usr/bin/env python3
"""Suggest private local-to-benchmark identity mappings; never write or approve."""

import argparse
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS / "lib"))

from artificial_analysis import cache_dir, load_snapshot  # noqa: E402
from model_evidence import (  # noqa: E402
    load_catalogues,
    load_private_mappings,
    sources_dir,
)
from model_evidence_onboarding import build_suggestions  # noqa: E402
from tier_resolve import list_local_ollama_models, resolve_roles_from_list  # noqa: E402


def _models_from_file(path):
    if path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("model inventory file is symlinked or oversized")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not 1 <= len(payload) <= 256:
        raise ValueError("model inventory must be a bounded non-empty array")
    for row in payload:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("name"), str)
            or not row["name"].strip()
            or row.get("provider", "ollama") not in ("ollama", "omlx")
        ):
            raise ValueError("invalid local model inventory row")
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument(
        "--models-file", type=Path,
        help="Optional offline array of local models (default: active engine discovery)",
    )
    parser.add_argument(
        "--source-dir", type=Path, default=sources_dir(),
        help="Private already-refreshed model evidence source directory",
    )
    parser.add_argument(
        "--aa-cache-dir", type=Path, default=cache_dir(),
        help="Private Artificial Analysis cache location",
    )
    parser.add_argument(
        "--map-file", type=Path,
        help="Optional existing private mapping for read-only status",
    )
    parser.add_argument(
        "--profile", choices=("quiet", "loud", "solo"),
        help="Apply matching local profile policy before suggesting mappings",
    )
    args = parser.parse_args()
    try:
        models = (
            _models_from_file(args.models_file)
            if args.models_file
            else list_local_ollama_models()
        )
        if not models:
            raise ValueError("no discovered local models; no mapping suggestions")
        aa = load_snapshot(args.aa_cache_dir)
        sources = load_catalogues(args.source_dir, aa_evidence=aa or {})
        mappings = load_private_mappings(args.map_file)
        classified = {}

        def collect(categories):
            classified.update(categories)

        resolve_roles_from_list(
            models,
            profile=args.profile,
            evidence_mode="off",
            category_observer=collect,
        )
        output = build_suggestions(
            classified,
            sources,
            aa_snapshot=aa,
            directory=args.source_dir,
            mappings=mappings,
        )
    except (OSError, ValueError, TypeError) as error:
        # Do not echo private source payloads or inventory contents.
        print(
            "Cannot suggest mappings from current private caches/inventory: "
            + type(error).__name__,
            file=sys.stderr,
        )
        return 1
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
