#!/usr/bin/env python3
"""Verify that tier model tables and the slim tier registry stay in sync."""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Set

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "scripts" / "lib"))
from litellm_aliases import resolve_canonical_identity  # noqa: E402

SLIM_PATH = ROOT / "configs" / "opencode" / "oh-my-opencode-slim.json"
DOCS_PATH = ROOT / "docs" / "TIERS.md"

HEADING = re.compile(r"^### .* Tier(?: \(`[^`]+`\))?\s*$")
TABLE_ROW = re.compile(r"^\|\s*[^|]+\|\s*([^|]+)\|.*$")
BACKTICKED_MODEL = re.compile(r"`([^`]+)`")


def _model_id(model: str) -> str:
    """Compare provider-qualified and unqualified documentation IDs equally."""
    if model.startswith("litellm/"):
        model = resolve_canonical_identity(model, True) or model
    return model.split("/", 1)[-1]


def registry_models(registry: dict) -> Set[str]:
    """Return all concrete primary models referenced by tier role presets."""
    models = set()
    for preset in registry.get("presets", {}).values():
        if not isinstance(preset, dict):
            continue
        for role in preset.values():
            if isinstance(role, dict) and isinstance(role.get("model"), str):
                models.add(_model_id(role["model"]))
    return models


def registry_tiers(registry: dict) -> Set[str]:
    """Return tier names from the authoritative tier preset registry."""
    return set(registry.get("presets", {}))


def documented_tiers(document: str) -> Set[str]:
    """Extract tier IDs from the tier definition table."""
    tiers = set()
    in_table = False
    for line in document.splitlines():
        if not in_table:
            if re.match(r"^\|\s*Tier\s*\|\s*Providers\s*\|\s*Best For\s*\|\s*$", line):
                in_table = True
            continue
        if not line.startswith("|"):
            break
        match = re.match(r"^\|\s*\*\*([^*]+)\*\*\s*\|", line)
        if match:
            tiers.add(match.group(1))
    return tiers


def documented_models(document: str) -> Set[str]:
    """Extract model IDs from Markdown rows within tier definition tables."""
    models = set()
    in_tier = False
    for line in document.splitlines():
        if HEADING.match(line):
            in_tier = True
            continue
        if in_tier and line.startswith("### "):
            in_tier = False
        if not in_tier:
            continue
        match = TABLE_ROW.match(line)
        if match:
            models.update(
                _model_id(model) for model in BACKTICKED_MODEL.findall(match.group(1))
            )
    return models


def drift_messages(registry: dict, document: str) -> list[str]:
    registry_set = registry_models(registry)
    docs_set = documented_models(document)
    missing = sorted(registry_set - docs_set)
    extra = sorted(docs_set - registry_set)
    messages = []
    if missing:
        messages.append("Missing from docs/TIERS.md tier tables: " + ", ".join(missing))
    if extra:
        messages.append("Extra in docs/TIERS.md tier tables: " + ", ".join(extra))
    return messages


def tier_set_messages(registry: dict, document: str) -> list[str]:
    """Compare the registry tier set against the tier definition table.

    Kept separate from `drift_messages` because synthetic documents used by
    focused tests exercise model-identity drift without tier tables.
    """
    registry_tier_set = registry_tiers(registry)
    docs_tier_set = documented_tiers(document)
    if registry_tier_set != docs_tier_set:
        return [
            "Tier count mismatch: registry has "
            f"{len(registry_tier_set)} tiers, docs table has {len(docs_tier_set)}; "
            "registry-only: "
            + ", ".join(sorted(registry_tier_set - docs_tier_set))
            + "; docs-only: "
            + ", ".join(sorted(docs_tier_set - registry_tier_set))
        ]
    return []


def check(slim_path: Path = SLIM_PATH, docs_path: Path = DOCS_PATH) -> int:
    try:
        registry = json.loads(slim_path.read_text(encoding="utf-8"))
        document = docs_path.read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Could not check tier documentation: {exc}", file=sys.stderr)
        return 1

    messages = drift_messages(registry, document) + tier_set_messages(
        registry, document
    )
    if messages:
        print("Tier documentation drift detected:", file=sys.stderr)
        for message in messages:
            print(f"  {message}", file=sys.stderr)
        return 2
    print("Tier documentation matches the slim tier registry.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slim", type=Path, default=SLIM_PATH)
    parser.add_argument("--docs", type=Path, default=DOCS_PATH)
    args = parser.parse_args()
    return check(args.slim, args.docs)


if __name__ == "__main__":
    raise SystemExit(main())
