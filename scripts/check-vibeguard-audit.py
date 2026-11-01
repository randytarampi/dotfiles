#!/usr/bin/env python3
"""Optionally run a read-only VibeGuard repository audit without enabling fixes.

Uses npx with @taehwandev/vibeguard@26.40.1 for a reproducible pilot. Bump
that exact pin only after reviewing and validating a newer upstream release.
"""

import argparse
import subprocess  # nosec B404 - pinned-package audit wrapper; fixed argv surface.
import sys
from pathlib import Path

PACKAGE = "@taehwandev/vibeguard@26.40.1"
MUTATING_SUBCOMMANDS = {"setup", "update", "hook", "evidence"}


def build_command(target, full=False, changed_only=False, strict=False):
    command = ["npx", "-y", "--package", PACKAGE, "vibeguard", "audit", str(target)]
    if full:
        command.append("--full")
    if changed_only:
        command.append("--changed-only")
    if strict:
        command.append("--strict")
    return command


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # Pre-parse guard (decisive): mutating tokens are rejected before
    # argparse ever sees them, so no mutating flag is ever declared or
    # accepted; unknown-but-benign flags fall through to argparse's usage
    # error (exit 2) instead of being silently forwarded.
    for token in argv:
        text = str(token)
        if text == "--fix" or text.startswith("--fix") or " --fix " in text:
            print(
                "read-only wrapper rejects --fix; mutating flags are never allowed",
                file=sys.stderr,
            )
            raise SystemExit(2)
        if text in MUTATING_SUBCOMMANDS:
            print(
                f"read-only wrapper rejects mutating subcommand: {text}",
                file=sys.stderr,
            )
            raise SystemExit(2)
    parser = argparse.ArgumentParser(
        description=(
            "Run a read-only VibeGuard audit. Scans do not modify files; this "
            "wrapper never passes fix/mutating flags of any kind."
        ),
        allow_abbrev=False,
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help="scan target (default: repo root, or CWD outside a repo)",
    )
    parser.add_argument("--full", action="store_true", help="request a full scan")
    parser.add_argument(
        "--changed-only", action="store_true", help="scan git-changed files only"
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="request the audit tool's strict mode (read-only; still never fixes)",
    )
    args = parser.parse_args(argv)

    target = args.target
    if target is None:
        try:
            root = subprocess.run(  # nosec B603, B607 - literal argv resolved from PATH; read-only helper.
                ["git", "rev-parse", "--show-toplevel"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            target = root or str(Path.cwd())
        except (OSError, subprocess.CalledProcessError):
            target = str(Path.cwd())
    return subprocess.run(  # nosec B603, B607 - literal npx argv; args validated before parse and by contract.
        build_command(target, args.full, args.changed_only, args.strict)
    ).returncode


if __name__ == "__main__":
    sys.exit(main())
