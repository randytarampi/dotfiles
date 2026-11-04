#!/usr/bin/env python3
"""Shared domain extraction from ddns-zones.json."""

import json
from dataclasses import dataclass
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
            if name and not name.startswith("*."):
                domains.add(name)

    return sorted(domains)


@dataclass(frozen=True)
class HostServices:
    openwebui_enabled: bool = False
    litellm_ui_exposed: bool = False
    paseo_enabled: bool = False


def build_hosts_entries(
    domains: list[str], access_mode: str, services: HostServices | None = None
) -> list[str]:
    """Build managed loopback hosts entries for the local Caddy sites."""
    if services is None:
        services = HostServices()
    marker = "# dotfiles-caddy-managed"
    entries = [
        f"127.0.0.1\topencode.localhost\t{marker}",
        f"::1\t\topencode.localhost\t{marker}",
    ]
    if services.paseo_enabled:
        entries.extend(
            [
                f"127.0.0.1\tpaseo.localhost\t{marker}",
                f"::1\t\tpaseo.localhost\t{marker}",
            ]
        )
    if services.openwebui_enabled:
        entries.extend(
            [
                f"127.0.0.1\tchat.localhost\t{marker}",
                f"::1\t\tchat.localhost\t{marker}",
            ]
        )
    served_domains = (
        [domain for domain in domains if domain.startswith("local.")]
        if access_mode == "localhost"
        else domains
    )
    for domain in served_domains:
        entries.extend(
            [
                f"127.0.0.1\t{domain}\t{marker}",
                f"::1\t\t{domain}\t{marker}",
                f"127.0.0.1\topencode.{domain}\t{marker}",
            ]
        )
        if services.openwebui_enabled:
            entries.append(f"127.0.0.1\tchat.{domain}\t{marker}")
        if services.paseo_enabled:
            entries.append(f"127.0.0.1\tpaseo.{domain}\t{marker}")
        if services.litellm_ui_exposed and access_mode in {"lan", "public"}:
            entries.append(f"127.0.0.1\tlitellm.{domain}\t{marker}")
    return entries
