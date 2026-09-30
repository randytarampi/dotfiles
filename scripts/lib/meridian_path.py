#!/usr/bin/env python3
"""Resolve Meridian's installed plugin and executable deterministically."""

from __future__ import annotations

import os
import re
import shutil
import subprocess  # nosec B404 - npm root invocation uses fixed arguments.
from pathlib import Path

import logger

PLUGIN_SUFFIX = Path("@rynfar/meridian/plugin/meridian.ts")


def _version_key(path: Path) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", path.name)
    return tuple(int(number) for number in numbers) or (0,)


def _nvm_roots() -> list[Path]:
    versions = (
        Path(os.environ.get("NVM_DIR", Path.home() / ".nvm")).expanduser()
        / "versions/node"
    )
    if not versions.is_dir():
        return []
    candidates = sorted(
        (entry for entry in versions.iterdir() if (entry / "bin/node").exists()),
        key=_version_key,
        reverse=True,
    )
    default_alias = versions.parent.parent / "alias/default"
    if default_alias.is_file():
        default = default_alias.read_text(encoding="utf-8").strip()
        preferred = [path for path in candidates if path.name == default]
        candidates = preferred + [path for path in candidates if path not in preferred]
    return candidates


def _global_npm_root() -> Path | None:
    npm = shutil.which("npm")
    if not npm:
        return None
    try:
        result = subprocess.run(
            [
                npm,
                "root",
                "-g",
            ],  # nosec B603 - npm path is resolved from PATH with fixed args.
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return (
        Path(result.stdout.strip()).expanduser()
        if result.returncode == 0 and result.stdout.strip()
        else None
    )


def _roots() -> list[tuple[str, Path]]:
    roots: list[tuple[str, Path]] = []
    explicit = os.environ.get("MERIDIAN_PLUGIN_PATH", "").strip()
    if explicit:
        roots.append(
            (
                "MERIDIAN_PLUGIN_PATH",
                Path(explicit).expanduser().parent.parent.parent.parent,
            )
        )
    npm_root = _global_npm_root()
    if npm_root:
        roots.append(("npm root -g", npm_root))
    roots.extend(
        (f"NVM {root.name}", root / "lib/node_modules") for root in _nvm_roots()
    )
    roots.extend(
        (f"system prefix {prefix}", Path(prefix) / "lib/node_modules")
        for prefix in ("/opt/homebrew", "/usr/local", "/usr")
    )
    return roots


def resolve_meridian_plugin_path() -> str | None:
    explicit = os.environ.get("MERIDIAN_PLUGIN_PATH", "").strip()
    explicit_path = Path(explicit).expanduser()
    if (
        explicit
        and explicit_path.is_file()
        and str(explicit_path).endswith(str(PLUGIN_SUFFIX))
    ):
        logger.info("Meridian plugin selected from MERIDIAN_PLUGIN_PATH: %s", explicit)
        return str(explicit_path)
    for provenance, root in _roots():
        candidate = root / PLUGIN_SUFFIX
        if candidate.is_file():
            logger.info("Meridian plugin selected from %s: %s", provenance, candidate)
            return str(candidate)
    return None


def resolve_meridian_binary() -> str | None:
    npm_root = _global_npm_root()
    roots = [("npm root -g", npm_root)] if npm_root else []
    for provenance, root in roots:
        for candidate in (
            root.parent / "bin/meridian",
            root.parent.parent / "bin/meridian",
        ):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                logger.info(
                    "Meridian executable selected from %s: %s", provenance, candidate
                )
                return str(candidate)
    active = shutil.which("meridian")
    if active:
        logger.info("Meridian executable selected from PATH: %s", active)
        return active
    fallback_roots = [
        *((f"NVM {root.name}", root / "lib/node_modules") for root in _nvm_roots()),
        *(
            (f"system prefix {prefix}", Path(prefix) / "lib/node_modules")
            for prefix in ("/opt/homebrew", "/usr/local", "/usr")
        ),
    ]
    for provenance, root in fallback_roots:
        for candidate in (
            root.parent / "bin/meridian",
            root.parent.parent / "bin/meridian",
        ):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                logger.info(
                    "Meridian executable selected from %s: %s", provenance, candidate
                )
                return str(candidate)
    return None


if __name__ == "__main__":
    import argparse
    import contextlib
    import logging
    import sys

    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", action="store_true")
    args = parser.parse_args()
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.StreamHandler) and handler.stream is sys.stdout:
            handler.setStream(sys.stderr)
    with contextlib.redirect_stdout(sys.stderr):
        resolved = (
            resolve_meridian_binary() if args.binary else resolve_meridian_plugin_path()
        )
    if resolved:
        print(resolved)
    raise SystemExit(0 if resolved else 1)
