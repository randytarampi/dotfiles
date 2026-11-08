#!/usr/bin/env python3
"""Summarize operator-supplied local utility-model measurements, offline only.

Never promotes model aliases or infers an entitlement from catalogue pricing.
"""

import argparse
import json
import re
from pathlib import Path
from statistics import median

MAX_INPUT_BYTES = 1_048_576
LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,95}\Z")

REQUIRED = (
    "device",
    "model",
    "task",
    "latency_ms",
    "peak_gb",
    "passed",
    "network_egress",
)


def validate(record):
    if not isinstance(record, dict):
        raise ValueError("measurement must be an object")
    if any(key not in record for key in REQUIRED):
        raise ValueError("measurement missing required nonsecret fields")
    if any(
        not isinstance(record[key], str) or not LABEL_RE.fullmatch(record[key])
        for key in ("device", "model", "task")
    ):
        raise ValueError("device, model and task must be nonempty strings")
    for key in ("latency_ms", "peak_gb"):
        number = record[key]
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise ValueError(f"{key} must be numeric")
        if not 0 <= number < float("inf"):
            raise ValueError(f"{key} must be finite and nonnegative")
    if not all(type(record[k]) is bool for k in ("passed", "network_egress")):
        raise ValueError("passed and network_egress must be booleans")
    if any(k not in (*REQUIRED, "provenance") for k in record):
        raise ValueError("unexpected field: redaction required before ingestion")
    if "provenance" in record and record["provenance"] not in (
        "owner-reported",
        "synthetic",
        "unverified",
    ):
        raise ValueError("unsupported measurement provenance")


def summarize(records):
    if not isinstance(records, list):
        raise ValueError("benchmark input must be an array")
    groups = {}
    for record in records:
        validate(record)
        provenance = record.get("provenance", "unverified")
        key = (record["device"], record["model"], record["task"], provenance)
        groups.setdefault(key, []).append(record)
    summary = []
    for (device, model, task, provenance), rows in sorted(groups.items()):
        summary.append(
            {
                "device": device,
                "model": model,
                "task": task,
                "count": len(rows),
                "median_latency_ms": median(r["latency_ms"] for r in rows),
                "peak_observed_gb": max(r["peak_gb"] for r in rows),
                "all_passed": all(r["passed"] for r in rows),
                "any_network_egress": any(r["network_egress"] for r in rows),
                "provenance": [provenance],
                "admission": "NOT_AUTO_PROMOTED",
            }
        )
    return summary


def load_results(path):
    """Bound the entire input before parsing and never follow a named symlink."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("results must be an existing nonsymlink regular file")
    with path.open("rb") as stream:
        raw = stream.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("results exceed the 1 MiB read limit")
    return json.loads(raw.decode("utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results", required=True, type=Path, help="local redacted JSON array"
    )
    args = parser.parse_args(argv)
    try:
        rows = load_results(args.results)
        print(json.dumps({"summary": summarize(rows)}, indent=2, sort_keys=True))
        return 0
    except (OSError, UnicodeError, ValueError) as exc:
        parser.exit(2, f"local-utility-benchmarks: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
