#!/usr/bin/env python3
"""Write a review-only OpenCode Zen free-model inventory artefact."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
import sys

sys.path.insert(0, str(SCRIPT_DIR / "lib"))

from model_catalogues import get_catalogue

ZEN_MODELS_URL = "https://opencode.ai/zen/v1/models"
DEFAULT_OUTPUT = REPO_ROOT / "artifacts/model-catalogues/opencode-zen-free.json"


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _all_zero_numeric(value) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, dict):
        return bool(value) and all(_all_zero_numeric(child) for child in value.values())
    if isinstance(value, (list, tuple)):
        return bool(value) and all(_all_zero_numeric(child) for child in value)
    if isinstance(value, (int, float, str)):
        try:
            return float(value) == 0
        except (TypeError, ValueError):
            return False
    return False


def is_zero_priced(pricing) -> bool:
    if (
        not isinstance(pricing, dict)
        or "prompt" not in pricing
        or "completion" not in pricing
    ):
        return False
    return _all_zero_numeric(pricing)


def write_artifact(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def refresh(output: Path = DEFAULT_OUTPUT) -> int:
    fetched_at = timestamp()
    api_key = os.environ.get("OPENCODE_API_KEY", "")
    if not api_key:
        write_artifact(
            output,
            {
                "status": "unavailable",
                "source": ZEN_MODELS_URL,
                "fetched_at": fetched_at,
                "reason": "OPENCODE_API_KEY is not set",
            },
        )
        return 0

    try:
        catalogue = get_catalogue(ZEN_MODELS_URL, api_key)
    except Exception as exc:
        write_artifact(
            output,
            {
                "status": "unavailable",
                "source": ZEN_MODELS_URL,
                "fetched_at": fetched_at,
                "reason": str(exc),
            },
        )
        return 0

    models = [
        {"id": item["id"], "pricing": item["pricing"]}
        for item in catalogue.get("data", [])
        if isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and is_zero_priced(item.get("pricing"))
    ]
    write_artifact(
        output,
        {
            "status": "available",
            "source": ZEN_MODELS_URL,
            "fetched_at": fetched_at,
            "models": models,
        },
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    return refresh(args.output)


if __name__ == "__main__":
    raise SystemExit(main())
