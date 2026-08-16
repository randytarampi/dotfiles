#!/usr/bin/env python3
# pyright: reportMissingImports=false
"""Configure ddns-route53: generate per-zone YAML configs from ddns-zones.json."""

import argparse
import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(os.path.dirname(os.path.realpath(__file__)))
LIB_DIR = SCRIPT_DIR / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import logger
from env import load_env
from file_utils import write_text_file
from cli_helpers import add_common_args


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate per-zone ddns-route53 configs from ddns-zones.json."
    )
    add_common_args(parser)
    return parser.parse_args()


def resolve_path(raw_path: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(raw_path)))


def get_zones_config_path() -> Path:
    return resolve_path(
        os.environ.get("CADDY_ZONES_CONFIG", "~/.config/caddy/ddns-zones.json")
    )


def get_config_dir() -> Path:
    return Path.home() / ".config" / "ddns-route53"


def quote_yaml(value: object) -> str:
    return json.dumps(value if isinstance(value, str) else str(value))


def render_zone_config(zone_id: str, records: list[dict[str, object]]) -> str:
    lines: list[str] = [
        "credentials:",
        f"  accessKeyID: {quote_yaml(os.environ.get('ROUTE53_AWS_ACCESS_KEY_ID', ''))}",
        f"  secretAccessKey: {quote_yaml(os.environ.get('ROUTE53_AWS_SECRET_ACCESS_KEY', ''))}",
        "",
        "route53:",
        f"  hostedZoneID: {quote_yaml(zone_id)}",
    ]

    if records:
        lines.append("  recordsSet:")
        for record in records:
            ttl_value = int(str(record["ttl"]))
            lines.extend(
                [
                    f"    - name: {quote_yaml(record['name'])}",
                    f"      type: {quote_yaml(record['type'])}",
                    f"      ttl: {ttl_value}",
                ]
            )
    else:
        lines.append("  recordsSet: []")

    lines.append("")
    return "\n".join(lines)


def main() -> None:
    args = parse_args()

    if not load_env():
        logger.warning("~/.env not found")

    ddns_gate = os.environ.get(
        "DOTFILES_RUN_DDNS_SETUP",
        os.environ.get("DOTFILES_RUN_CADDY_SETUP", "0"),
    )
    if ddns_gate != "1":
        logger.info(
            f"DOTFILES_RUN_DDNS_SETUP='{ddns_gate}' — skipping ddns-route53 configuration"
        )
        return

    zones_config_path = get_zones_config_path()
    if not zones_config_path.exists():
        logger.warning("ddns-zones.json not found — skipping")
        return

    try:
        zones_data = json.loads(zones_config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.critical(f"Failed to parse ddns zones config {zones_config_path}: {exc}")
        raise SystemExit(1)

    zones = zones_data.get("zones", [])
    if not isinstance(zones, list):
        logger.critical(
            f"Invalid ddns zones config: 'zones' must be a list ({zones_config_path})"
        )
        raise SystemExit(1)

    config_dir = get_config_dir()
    written_paths: list[tuple[str, Path]] = []

    for zone in zones:
        if not isinstance(zone, dict):
            logger.warning("Skipping invalid ddns zone entry")
            continue

        zone_id = str(zone.get("hostedZoneId", "")).strip()
        if not zone_id:
            logger.warning("Skipping ddns zone without hostedZoneId")
            continue

        raw_records = zone.get("records", [])
        if not isinstance(raw_records, list):
            logger.warning(f"Skipping ddns zone {zone_id}: records must be a list")
            continue

        records: list[dict[str, object]] = []
        for record in raw_records:
            if not isinstance(record, dict):
                logger.warning(f"Skipping invalid ddns record in zone {zone_id}")
                continue

            name = str(record.get("name", "")).strip()
            record_type = str(record.get("type", "")).strip().upper()
            ttl = record.get("ttl")
            if not name or not record_type or ttl is None:
                logger.warning(f"Skipping incomplete ddns record in zone {zone_id}")
                continue

            try:
                ttl_value = int(ttl)
            except (TypeError, ValueError):
                logger.warning(
                    f"Skipping ddns record with invalid ttl in zone {zone_id}"
                )
                continue

            records.append({"name": name, "type": record_type, "ttl": ttl_value})

        config_path = config_dir / f"zone-{zone_id}.yml"
        config_text = render_zone_config(zone_id, records)
        if args.dry_run:
            logger.info(f"Would write DDNS zone config to {config_path}")
        else:
            write_text_file(str(config_path), config_text, backup=True)
        written_paths.append((zone_id, config_path))

    summary_lines = [
        f"ddns-route53 configured for {len(written_paths)} zone(s)!",
        "",
    ]
    if written_paths:
        summary_lines.extend(
            f"  • {zone_id}: {path}" for zone_id, path in written_paths
        )
    else:
        summary_lines.append(f"Zone config source: {zones_config_path}")
    logger.info("\n".join(summary_lines))


if __name__ == "__main__":
    main()
