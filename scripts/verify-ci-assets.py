#!/usr/bin/env python3
"""Verify hashes for CI/local-only assets tracked outside chezmoi triggers."""

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "configs/review/assets-manifest.json"


def main():
    argparse.ArgumentParser(
        description="Verify hashes for CI and local review assets",
        allow_abbrev=False,
    ).parse_args()
    try:
        assets = json.loads(MANIFEST.read_text(encoding="utf-8"))["assets"]
    except (OSError, json.JSONDecodeError, KeyError) as error:
        print(f"Could not read CI asset manifest: {error}")
        return 1

    drift = []
    for name, expected in assets.items():
        path = ROOT / name
        if not path.is_file():
            drift.append(f"{name}: missing")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            drift.append(f"{name}: expected {expected}, found {actual}")

    if drift:
        print("CI asset hash drift detected:")
        print("\n".join(f"  - {item}" for item in drift))
        print("Regenerate configs/review/assets-manifest.json (make update-ci-assets).")
        return 1
    print(f"CI asset hashes verified ({len(assets)} assets).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
