#!/usr/bin/env python3
"""Validate all staged JSON operands without writing to any of them."""

import json
import sys
from pathlib import Path


def main(paths):
    if not paths:
        print("precommit-json-check: no JSON paths supplied", file=sys.stderr)
        return 2
    failures = 0
    for filename in paths:
        path = Path(filename)
        try:
            with path.open(encoding="utf-8") as reader:
                json.load(reader)
        except (OSError, UnicodeError, ValueError) as exc:
            print(f"{path}: invalid JSON ({exc})", file=sys.stderr)
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
