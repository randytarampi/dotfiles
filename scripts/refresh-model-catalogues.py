#!/usr/bin/env python3
"""Write a review-only OpenCode Console free-model candidate inventory."""

from __future__ import annotations

import argparse
import json
import math
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
from models_dev import MODELS_DEV_URL, fetch_models_dev_snapshot  # noqa: E402
from catalogue_review import (  # noqa: E402
    SCHEMA_VERSION,
    provider_coverage,
    registry_review,
)

CONSOLE_MODELS_URL = "https://opencode.ai/inference/v1/models"
DEFAULT_OUTPUT = REPO_ROOT / "artifacts/model-catalogues/free-model-candidates.json"


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


def _valid_nonnegative_prices(value) -> bool:
    """Validate each reported price, including optional nested price tiers."""
    if isinstance(value, dict):
        return bool(value) and all(
            _valid_nonnegative_prices(child) for child in value.values()
        )
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return False
    try:
        amount = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(amount) and amount >= 0


def valid_pricing(pricing) -> bool:
    return (
        isinstance(pricing, dict)
        and "input" in pricing
        and "output" in pricing
        and isinstance(pricing["input"], (int, float, str))
        and isinstance(pricing["output"], (int, float, str))
        and _valid_nonnegative_prices(pricing)
    )


def is_zero_priced(pricing) -> bool:
    return valid_pricing(pricing) and _all_zero_numeric(pricing)


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
                "source": CONSOLE_MODELS_URL,
                "fetched_at": fetched_at,
                "reason": "OPENCODE_API_KEY is not set",
            },
            dry_run=dry_run,
        )
        return 1

    try:
        catalogue = get_catalogue(CONSOLE_MODELS_URL, api_key, strict=True)
        raw_models = catalogue["data"]
        if not isinstance(raw_models, list) or not raw_models:
            raise ValueError("Console model catalogue is empty or malformed")
        ids = []
        for item in raw_models:
            model_id = item.get("id") if isinstance(item, dict) else None
            if (
                not isinstance(model_id, str)
                or not model_id.strip()
                or model_id != model_id.strip()
            ):
                raise ValueError("Console model catalogue contains an invalid ID")
            ids.append(model_id)
        if len(ids) != len(set(ids)):
            raise ValueError("Console model catalogue contains duplicate IDs")
    except Exception:
        # External errors may include request details; do not print credentials.
        print(
            "OpenCode Console catalogue unavailable or malformed",
            file=sys.stderr,
        )
        write_artifact(
            output,
            {
                "status": "unavailable",
                "source": CONSOLE_MODELS_URL,
                "fetched_at": fetched_at,
                "reason": "Console model catalogue unavailable or malformed",
            },
            dry_run=dry_run,
        )
        return 1

    try:
        snapshot = fetch_models_dev_snapshot()
        provider = snapshot["models"]["opencode"]
        prices = provider["models"]
        observed = snapshot["observed_at"]
        expires = snapshot["expires_at"]
        if (
            not isinstance(prices, dict)
            or not isinstance(observed, str)
            or not isinstance(expires, str)
        ):
            raise ValueError("models.dev pricing evidence is malformed")
    except Exception:
        print("OpenCode pricing metadata unavailable", file=sys.stderr)
        write_artifact(
            output,
            {
                "status": "unavailable",
                "source": CONSOLE_MODELS_URL,
                "pricing_source": MODELS_DEV_URL,
                "fetched_at": fetched_at,
                "reason": "models.dev pricing metadata unavailable",
                "counts": {
                    "catalogue": len(ids),
                    "classified": 0,
                    "free": 0,
                    "unclassified": len(ids),
                },
            },
            dry_run=dry_run,
        )
        return 1

    free_models = []
    unclassified = []
    classified = 0
    for model_id in ids:
        record = prices.get(model_id)
        cost = record.get("cost") if isinstance(record, dict) else None
        if not valid_pricing(cost):
            unclassified.append(model_id)
            continue
        classified += 1
        if is_zero_priced(cost):
            free_models.append({"id": model_id, "pricing": cost})

    models = normalize_models(free_models)
    review = registry_review(models, ids)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "provider_coverage": provider_coverage(CONSOLE_MODELS_URL),
        "review": review,
        "status": "incomplete" if unclassified else "available",
        "source": CONSOLE_MODELS_URL,
        "pricing_source": MODELS_DEV_URL,
        "fetched_at": fetched_at,
        "pricing_observed_at": observed,
        "pricing_expires_at": expires,
        "counts": {
            "catalogue": len(ids),
            "classified": classified,
            "free": len(models),
            "unclassified": len(unclassified),
        },
        "models": models,
    }
    if unclassified:
        payload["unclassified_model_ids"] = sorted(unclassified)
        print(
            "OpenCode pricing incomplete: "
            f"{len(unclassified)} of {len(ids)} Console models lack valid cost data",
            file=sys.stderr,
        )
    elif output.is_file():
        try:
            previous = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = None
        if (
            isinstance(previous, dict)
            and previous.get("status") in {"available", "unchanged"}
            and previous.get("source") == payload["source"]
            and previous.get("pricing_source") == payload["pricing_source"]
            and previous.get("counts") == payload["counts"]
            and previous.get("schema_version") == SCHEMA_VERSION
            and previous.get("provider_coverage") == payload["provider_coverage"]
            and previous.get("review") == payload["review"]
            and isinstance(previous.get("models"), list)
            and normalize_models(previous["models"]) == payload["models"]
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
