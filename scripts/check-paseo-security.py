#!/usr/bin/env python3
"""Read-only Paseo web/password/Caddy audit; never print secret values."""

import argparse
import json
import os
import sys

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from env import load_env  # noqa: E402
from paseo_web_policy import domains_for_access, read_proxy_config  # noqa: E402
from paseo_security import audit  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--access", choices=["localhost", "lan", "public"])
    args = parser.parse_args(argv)
    try:
        # Match configure-caddy.py: inspect the repository's actual configured
        # environment, not a shell's potentially incomplete exported subset.
        if not load_env():
            raise ValueError("canonical ~/.env unavailable; cannot audit Caddy")
        access = args.access or os.environ.get("CADDY_ACCESS", "localhost")
        caddy = os.environ.get("DOTFILES_RUN_CADDY_SETUP", "0") == "1"
        # Use the same generated host calculation as Caddy for every mode.
        # Localhost can include paseo.local.* aliases from configured zones.
        # This is not live DNS, TLS or certificate discovery.
        required = domains_for_access(access)
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
