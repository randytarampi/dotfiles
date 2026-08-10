#!/usr/bin/env python3
"""Deprecated thin wrapper — delegates to configure-project.py."""

import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))


def main():
    if os.environ.get("PROJECT_CONFIG_DELEGATE") == "1":
        result = subprocess.run(
            [
                sys.executable,
                os.path.join(SCRIPT_DIR, "_configure-jetbrains-workspace-project.py"),
            ]
            + sys.argv[1:]
        )
        raise SystemExit(result.returncode)
    args = sys.argv[1:]
    command = [
        sys.executable,
        os.path.join(SCRIPT_DIR, "configure-project.py"),
        "--steps",
        "jetbrains",
    ]
    if "--workspace-root" not in args and "--project-dir" not in args:
        command.extend(["--workspace-root", os.getcwd()])
    command.extend(args)
    result = subprocess.run(command)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
