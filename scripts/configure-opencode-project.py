#!/usr/bin/env python3
"""Deprecated thin wrapper — delegates to configure-project.py."""

import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))


def main():
    args = sys.argv[1:]
    mapped = []
    i = 0
    while i < len(args):
        if args[i] == "--project-mcps":
            mapped.extend(["--mcps", args[i + 1]])
            i += 2
        elif args[i] == "--mcps":
            mapped.extend(["--mcp-tools", args[i + 1]])
            i += 2
        elif args[i] == "--all-mcps":
            i += 1
        else:
            mapped.append(args[i])
            i += 1
    result = subprocess.run(
        [
            sys.executable,
            os.path.join(SCRIPT_DIR, "configure-project.py"),
            "--steps",
            "opencode,tier,codegraph,mcps",
        ]
        + mapped
    )
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
