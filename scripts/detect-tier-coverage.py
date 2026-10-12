#!/usr/bin/env python3
"""Report which Slim registry model selections the LiteLLM gateway inventory serves.

Reads the model ids a deploy generated into opencode.json (``provider.litellm``)
and resolves every preset, council, ``_tiers`` mirror, and fallback-chain
reference with the same semantics as the gateway-only Slim gate, so tier
servability problems surface before a deploy aborts on them instead of after.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import logger  # noqa: E402  # sys.path is bootstrapped for shared repo libraries.
from cli_helpers import add_common_args  # noqa: E402  # sys.path bootstrap.
from env import load_env  # noqa: E402  # sys.path bootstrap.
from tier_coverage import (  # noqa: E402
    load_inventory_from_opencode_json,
    unresolved_refs,
)

DEFAULT_SLIM = ROOT / "configs" / "opencode" / "oh-my-opencode-slim.json"
DEFAULT_INVENTORY = Path("~/.config/opencode/opencode.json").expanduser()


def parse_args(argv=None):
    """Parse registry, inventory, preset filter, and output options from argv."""
    parser = add_common_args(
        argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    )
    parser.add_argument(
        "--slim",
        type=Path,
        default=DEFAULT_SLIM,
        help="Slim registry document (default: the checked-in registry)",
    )
    parser.add_argument(
        "--inventory",
        type=Path,
        default=DEFAULT_INVENTORY,
        help="Generated opencode.json carrying provider.litellm model ids",
    )
    parser.add_argument(
        "--preset",
        default=None,
        help="Only report references whose dotted location contains this name",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    return parser.parse_args(argv)


def main() -> int:
    """Report unserved selections, returning 1 for registry errors or coverage gaps.

    Return 0 when all selected references are served or inventory is unknown.
    """
    args = parse_args()
    load_env()
    try:
        slim = loads_or_none(args.slim)
    except ValueError as error:
        logger.warning("Slim registry unavailable: %s", error)
        return 1
    if not isinstance(slim, dict):
        logger.warning("Slim registry is not a JSON object: %s", args.slim)
        return 1
    if not slim:
        logger.warning("Slim registry is empty or unreadable: %s", args.slim)
        return 1
    inventory = load_inventory_from_opencode_json(args.inventory)
    if inventory is None:
        # UNKNOWN discipline: no deployed inventory means nothing to prove;
        # never block (e.g. direct mode has no provider.litellm block).
        if args.json:
            print('{"status": "unknown"}')
        else:
            logger.info(
                "Gateway inventory unavailable at %s — UNKNOWN, non-blocking",
                args.inventory,
            )
        return 0
    unserved = unresolved_refs(slim, inventory)
    if args.preset:
        unserved = [
            entry
            for entry in unserved
            if f".{args.preset}." in entry.location
            or entry.location.startswith(args.preset)
        ]
    if args.json:
        print(
            json.dumps(
                {
                    "status": "served" if not unserved else "unserved",
                    "unserved": [
                        {"location": entry.location, "ref": entry.ref}
                        for entry in sorted(unserved, key=lambda item: item.location)
                    ],
                }
            )
        )
        return 0 if not unserved else 1
    if not unserved:
        logger.info("All Slim registry selections are served by the gateway")
        return 0
    lines = [
        f"{len(unserved)} Slim registry selection(s) served nowhere by the gateway:"
    ]
    for entry in sorted(unserved, key=lambda item: item.location):
        lines.append(f"  {entry.location} = {entry.ref}")
    logger.warning("\n".join(lines))
    return 1


def loads_or_none(path: Path):
    """Decode a JSON file, raising ValueError on read or JSON parsing failures."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(str(exc)) from exc


if __name__ == "__main__":
    raise SystemExit(main())
