#!/usr/bin/env python3
"""Shared domain extraction from ddns-zones.json."""

import json
from pathlib import Path


def load_domains(zones_path: Path) -> list[str]:
    """Load unique domain names from ddns-zones.json.

    Extracts domain names from all records across all zones.
    Strips trailing dots. Returns sorted unique list.
    Returns empty list if file missing or no domains.
    """
    if not zones_path.exists():
        return []

    try:
        with open(zones_path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    domains = set()
    for zone in data.get("zones", []):
        for record in zone.get("records", []):
            name = record.get("name", "").rstrip(".")
            if name:
                domains.add(name)

    return sorted(domains)
