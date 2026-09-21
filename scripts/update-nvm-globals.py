#!/usr/bin/env python3
"""Update npm global packages across installed nvm and Homebrew Node versions."""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
LIB_DIR = SCRIPT_DIR / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli_helpers  # noqa: E402
import logger  # noqa: E402

VERSION_RE = re.compile(r"v\d+\.\d+\.\d+")


def _brew_prefix():
    try:
        result = subprocess.run(
            ["brew", "--prefix"], capture_output=True, text=True, check=True
        )
        prefix = result.stdout.strip()
        if prefix:
            return Path(prefix)
    except (OSError, subprocess.CalledProcessError):
        pass
    return next(
        (
            Path(candidate)
            for candidate in ("/opt/homebrew", "/usr/local")
            if Path(candidate).exists()
        ),
        None,
    )


def _nvm_script():
    nvm_dir = Path.home() / ".nvm"
    local_script = nvm_dir / "nvm.sh"
    if local_script.is_file():
        return local_script
    prefix = _brew_prefix()
    candidate = prefix / "opt/nvm/nvm.sh" if prefix else None
    return candidate if candidate and candidate.is_file() else None


def _nvm(args, *, check=False):
    script = _nvm_script()
    if script is None:
        return subprocess.CompletedProcess(args, 127, "", "nvm.sh not found")
    command = f'source "{script}" && nvm {" ".join(args)}'
    return subprocess.run(
        ["bash", "-c", command], capture_output=True, text=True, check=check
    )


def _installed_versions(default_version, listing):
    versions = set(
        VERSION_RE.findall(
            "\n".join(
                line
                for line in listing.splitlines()
                if "default ->" not in line
                and not re.search(r"v\d+\.\d+\.\d+ -> system", line)
                and "N/A" not in line
            )
        )
    )
    versions.discard(default_version)
    return sorted(
        versions, key=lambda value: tuple(map(int, value[1:].split("."))), reverse=True
    )


def _system_npm():
    prefix = _brew_prefix()
    candidates = [prefix / "bin/npm"] if prefix else []
    candidates.extend(
        Path(path) / "bin/npm" for path in ("/opt/homebrew", "/usr/local")
    )
    return next(
        (path for path in candidates if path.is_file() and os.access(path, os.X_OK)),
        None,
    )


def update_globals(dry_run=False):
    Path.home().joinpath(".nvm").mkdir(parents=True, exist_ok=True)
    probe = _nvm(["version", "default"])
    if probe.returncode == 127:
        logger.error(
            "nvm not found. Ensure NVM_DIR=%s and nvm.sh is sourced.",
            Path.home() / ".nvm",
        )
        return 1
    default_version = probe.stdout.strip()
    if not default_version or default_version == "N/A":
        logger.error("No nvm default version set. Run: nvm alias default <version>")
        return 1

    logger.info("Default node version: %s", default_version)
    versions_result = _nvm(["ls", "--no-colors"])
    other_versions = _installed_versions(default_version, versions_result.stdout)
    logger.info("Updating global packages on %s...", default_version)
    if not dry_run:
        _nvm(["use", "default"])
        result = subprocess.run(["npm", "update", "-g"], check=False)
        if result.returncode:
            logger.warning("npm update -g on %s had failures", default_version)

    if other_versions:
        logger.info(
            "Propagating packages from %s to other versions...", default_version
        )
        for version in other_versions:
            logger.info(
                "Reinstalling packages on %s from %s...", version, default_version
            )
            if dry_run:
                continue
            if _nvm(["use", version]).returncode:
                logger.warning("nvm use %s failed — skipping", version)
                continue
            if _nvm(["reinstall-packages", "default"]).returncode:
                logger.warning("reinstall-packages on %s had failures", version)
    else:
        logger.info(
            "Only one installed version (%s). Nothing to propagate.", default_version
        )

    system_npm = _system_npm()
    system_version = "unknown"
    if system_npm:
        node = system_npm.parent / "node"
        try:
            system_version = (
                subprocess.run(
                    [str(node), "--version"],
                    capture_output=True,
                    text=True,
                    check=False,
                ).stdout.strip()
                or "unknown"
            )
        except OSError:
            pass
        logger.info("Updating global packages on system node (%s)...", system_version)
        if (
            not dry_run
            and subprocess.run(
                [str(system_npm), "update", "-g"], check=False
            ).returncode
        ):
            logger.warning("npm update -g on system node had failures")
    else:
        logger.info("No system (Homebrew) node found — skipping")

    if not dry_run:
        _nvm(["use", "default"])
    logger.info(
        "Global packages updated across all node versions.\n\nDefault: %s\nnvm versions: %s\nSystem node: %s\n\nUpdate script complete!",
        default_version,
        ", ".join(other_versions),
        system_version if system_npm else "none found",
    )
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    cli_helpers.add_common_args(parser)
    args = parser.parse_args()
    raise SystemExit(update_globals(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
