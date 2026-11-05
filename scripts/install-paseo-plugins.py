#!/usr/bin/env python3
"""Owner-gated Paseo plugin installation; never run from make deploy."""

import argparse
import os
import re
import shutil
import subprocess  # nosec B404 - resolved Paseo binary and fixed argv, no shell.
import sys

# Community plugins run unsandboxed inside the Paseo daemon. Owner must review
# source/revisions before enabling this explicit install action.
PLUGINS = (
    ("stevecastaneda/progress", None),
    ("paseo-cafe/cafe", None),
    ("uiyzzi/codexbar-usage", "codexbar"),
    ("roundreddot/better-paseo", None),
    ("gpambrozio/skills", None),
    ("dutchakdev/machine-status", "paseo-0.8-through-0.13"),
)
GATE = "DOTFILES_RUN_PASEO_PLUGINS_SETUP"


def version_tuple(value):
    match = re.fullmatch(r"(?:paseo\s+)?v?(\d+)\.(\d+)\.(\d+)", value.strip())
    if not match:
        raise ValueError("Paseo version unknown; refusing plugin installation")
    return tuple(int(part) for part in match.groups())


def qualified_node_version(which, run):
    """Read only the installed Node version; unknown is not qualified."""
    binary = which("node")
    if not binary or not os.path.isabs(binary):
        return None
    try:
        result = run(
            [binary, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return version_tuple(result.stdout) if result.returncode == 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def plan(
    which, version, *, platform=None, node_version=None, daemon_codexbar_verified=False
):
    """Preflight documented plugin prerequisites before any native mutation."""
    platform = sys.platform if platform is None else platform
    selected, omitted = [], []
    for name, requirement in PLUGINS:
        if name == "stevecastaneda/progress" and (
            node_version is None or node_version < (22, 18, 0)
        ):
            omitted.append((name, "Node >=22.18 required"))
        elif name == "uiyzzi/codexbar-usage" and (
            not daemon_codexbar_verified or not which("codexbar")
        ):
            omitted.append((name, "codexbar must be verified on daemon PATH"))
        elif name == "roundreddot/better-paseo" and version < (0, 12, 0):
            omitted.append((name, "Paseo >=0.12 required"))
        elif requirement == "paseo-0.8-through-0.13" and not (
            (0, 8, 0) <= version < (0, 14, 0)
        ):
            omitted.append((name, "published support ends at Paseo 0.13"))
        elif requirement == "paseo-0.8-through-0.13" and platform != "darwin":
            omitted.append((name, "macOS required"))
        else:
            selected.append(name)
    return selected, omitted


def install_selected(paseo, selected):
    """Call only the fixed, owner-approved native CLI plugin command."""
    for name in selected:
        result = subprocess.run(  # nosec B603 - fixed plugin add argv.
            [paseo, "plugin", "add", name],
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode:
            # Never print plugin subprocess output: it can include private paths.
            raise ValueError(
                f"plugin {name} installation failed (exit {result.returncode})"
            )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--apply", action="store_true", help="Install approved plugins")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writes")
    parser.add_argument(
        "--codexbar-daemon-verified",
        action="store_true",
        help="Owner attests codexbar is on the actual Paseo daemon PATH",
    )
    args = parser.parse_args(argv)
    if not args.apply and not args.dry_run:
        args.dry_run = True

    paseo = shutil.which("paseo")
    if not paseo:
        if args.apply and not args.dry_run:
            print("Paseo is not installed", file=sys.stderr)
            return 1
        print("Paseo not installed; no changes")
        return 0

    try:
        version_output = subprocess.run(  # nosec B603 - fixed --version argv.
            [paseo, "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        version = version_tuple(version_output.stdout)
        selected, omitted = plan(
            shutil.which,
            version,
            node_version=qualified_node_version(shutil.which, subprocess.run),
            daemon_codexbar_verified=args.codexbar_daemon_verified,
        )
        for name, reason in omitted:
            print(f"Skipping {name}: {reason}")
        for name in selected:
            print(f"{'Would install' if args.dry_run else 'Installing'} {name}")
        if args.dry_run:
            return 0
        if os.environ.get(GATE) != "1":
            raise ValueError(f"explicit --apply requires {GATE}=1")
        if selected:
            install_selected(paseo, selected)
            print("Paseo plugins installed; check installed state and reload daemon")
        else:
            print("No eligible plugins; no changes")
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Paseo plugin setup refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
