#!/usr/bin/env python3
"""Shared Ollama Cloud registry and stale-stub operations."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess  # nosec B404 - calls use fixed Ollama subcommands and validated names.
from pathlib import Path


def managed_models(path: Path) -> set[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return set(data.get("models", {}))


def stale_managed_stubs(installed: list[str], managed: set[str]) -> list[str]:
    expected = {
        f"{name}{suffix}" for name in managed for suffix in (":cloud", "-cloud")
    }
    return sorted(
        name
        for name in installed
        if name.endswith((":cloud", "-cloud")) and name not in expected
    )


def installed_models(ollama: str) -> list[str]:
    result = subprocess.run(  # nosec B603 - executable and args are controlled.
        [ollama, "list"], capture_output=True, text=True, check=False
    )
    return [line.split()[0] for line in result.stdout.splitlines()[1:] if line.split()]


def cleanup_stale(path: Path, ollama: str | None = None) -> int:
    ollama = ollama or shutil.which("ollama")
    if not ollama:
        return 0
    failed = 0
    for name in stale_managed_stubs(installed_models(ollama), managed_models(path)):
        if (
            subprocess.run(  # nosec B603 - model name comes from the validated registry/list.
                [ollama, "rm", name], check=False
            ).returncode
            != 0
        ):
            failed = 1
    return failed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--pull-list", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()
    if args.pull_list:
        print("\n".join(sorted(managed_models(args.registry))))
    if args.cleanup:
        return cleanup_stale(args.registry)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
