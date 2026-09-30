#!/usr/bin/env python3
"""Write a review-only OpenCode Zen free-model inventory artefact."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

sys.path.insert(0, str(SCRIPT_DIR / "lib"))

# sys.path must be bootstrapped before importing the shared library.
from cli_helpers import add_common_args  # noqa: E402
from model_catalogues import get_catalogue  # noqa: E402

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


def normalize_models(models: list[dict]) -> list[dict]:
    """Return semantically equivalent model entries in stable ID order."""
    return sorted(models, key=lambda model: model["id"])


def write_artifact(path: Path, payload: dict, *, dry_run: bool = False) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def refresh(output: Path = DEFAULT_OUTPUT, *, dry_run: bool = False) -> int:
    fetched_at = timestamp()
    api_key = os.environ.get("OPENCODE_API_KEY", "")
    if not api_key:
        print(
            "OpenCode catalogue unavailable: OPENCODE_API_KEY is not set",
            file=sys.stderr,
        )
        write_artifact(
            output,
            {
                "status": "unavailable",
                "source": ZEN_MODELS_URL,
                "fetched_at": fetched_at,
                "reason": "OPENCODE_API_KEY is not set",
            },
            dry_run=dry_run,
        )
        return 1

    try:
        catalogue = get_catalogue(ZEN_MODELS_URL, api_key, strict=True)
    except Exception as exc:
        print(f"OpenCode catalogue unavailable: {exc}", file=sys.stderr)
        write_artifact(
            output,
            {
                "status": "unavailable",
                "source": ZEN_MODELS_URL,
                "fetched_at": fetched_at,
                "reason": str(exc),
            },
            dry_run=dry_run,
        )
        return 1

    models = normalize_models(
        [
            {"id": item["id"], "pricing": item["pricing"]}
            for item in catalogue.get("data", [])
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and is_zero_priced(item.get("pricing"))
        ]
    )
    payload = {
        "status": "available",
        "source": ZEN_MODELS_URL,
        "fetched_at": fetched_at,
        "models": models,
    }
    if output.is_file():
        try:
            previous = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = None
        if (
            isinstance(previous, dict)
            and previous.get("status") in {"available", "unchanged"}
            and previous.get("source") == payload["source"]
            and isinstance(previous.get("models"), list)
            and normalize_models(previous.get("models", [])) == payload["models"]
        ):
            payload["status"] = "unchanged"
    write_artifact(output, payload, dry_run=dry_run)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        return refresh(args.output, dry_run=args.dry_run)
    except (OSError, ValueError) as exc:
        print(f"refresh-model-catalogues: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
