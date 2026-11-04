#!/usr/bin/env python3
"""Read a dedicated Claude Desktop gateway credential (no network or writes)."""

import argparse
import os
import shlex
import stat
import sys
from pathlib import Path

CLIENT_PATH = (".local", "share", "litellm", "clients", "claude-desktop.key")
MAX_BYTES = 4096


def credential():
    """Fail closed on symlinks, unsafe file modes, ownership and malformed keys."""
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("unsupported platform")
    directory = Path.home()
    for part in (None, *CLIENT_PATH[:-1]):
        if part:
            directory = directory / part
        metadata = directory.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o022
        ):
            raise ValueError("unsafe credential directory")
    path = directory / CLIENT_PATH[-1]
    expected = path.lstat()
    if (
        not stat.S_ISREG(expected.st_mode)
        or expected.st_uid != os.geteuid()
        or stat.S_IMODE(expected.st_mode) != 0o600
    ):
        raise ValueError("unsafe credential file")
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as source:
        actual = os.fstat(source.fileno())
        if (
            not stat.S_ISREG(actual.st_mode)
            or actual.st_uid != os.geteuid()
            or stat.S_IMODE(actual.st_mode) != 0o600
            or (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino)
        ):
            raise ValueError("credential changed during read")
        data = source.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("oversize credential")
    token = data.removesuffix(b"\n")
    if not token or any(byte < 33 or byte > 126 for byte in token):
        raise ValueError("invalid credential syntax")
    return token.decode("ascii")


def main(argv=None):
    parser = argparse.ArgumentParser(allow_abbrev=False, description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--check",
        action="store_true",
        help="Check key availability without disclosing it",
    )
    actions.add_argument(
        "--wrapper",
        action="store_true",
        help="Print a secret-free POSIX wrapper with absolute Python and helper paths",
    )
    args = parser.parse_args(argv)
    if args.wrapper:
        python = Path(sys.executable).resolve()
        helper = Path(__file__).resolve()
        if not python.is_file() or not helper.is_file():
            print("Claude Desktop helper executable unavailable", file=sys.stderr)
            return 1
        print("#!/bin/sh")
        print(f'exec {shlex.quote(str(python))} {shlex.quote(str(helper))} "$@"')
        return 0
    try:
        token = credential()
    except (OSError, ValueError):
        print("Claude Desktop credential unavailable or unsafe", file=sys.stderr)
        return 1
    if args.check:
        print("Claude Desktop credential ready")
    else:
        # Anthropic's helper-script contract explicitly requires stdout.
        sys.stdout.write(token + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
