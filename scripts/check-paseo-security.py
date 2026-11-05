#!/usr/bin/env python3
"""Read-only Paseo web/password/Caddy audit; never print secret values."""

import argparse
import json
import os
import sys

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from paseo_web_policy import (  # noqa: E402
    desired_hostnames,
    domains_for_access,
    read_proxy_config,
)
from paseo_security import audit  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--access", choices=["localhost", "lan", "public"])
    args = parser.parse_args(argv)
    try:
        access = args.access or os.environ.get("CADDY_ACCESS", "localhost")
        caddy = os.environ.get("DOTFILES_RUN_CADDY_SETUP", "0") == "1"
        # This read-only audit is not DNS/certificate discovery. The owner must
        # verify concrete Caddy routes on the live machine.
        required = (
            desired_hostnames("localhost", [])
            if access == "localhost"
            else domains_for_access(access)
        )
        status = audit(
            read_proxy_config(),
            access,
            caddy_enabled=caddy,
            required_hosts=required,
        )
        if args.json:
            print(json.dumps(status, indent=2, sort_keys=True))
        else:
            print("Paseo stored settings qualified; daemon runtime UNKNOWN")
            print("Caddy DNS, TLS, correct/wrong password and WebSocket tests: NOT RUN")
        return 0
    except (OSError, ValueError) as error:
        print("Paseo security audit refused: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
