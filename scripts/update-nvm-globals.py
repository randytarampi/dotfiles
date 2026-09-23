#!/usr/bin/env python3
"""Update npm global packages across installed nvm and Homebrew Node versions."""

import argparse
import os
import re
import shlex
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


def _nvm_script():
    nvm_dir = Path.home() / ".nvm"
    candidates = [nvm_dir / "nvm.sh"]
    candidates.extend(
        Path(prefix) / "opt/nvm/nvm.sh" for prefix in ("/opt/homebrew", "/usr/local")
    )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _compose_script(script, *, dry_run=False):
    if dry_run:
        return f"""set +e
source {shlex.quote(str(script))}
if ! command -v nvm >/dev/null 2>&1; then
  printf '%s\\n' 'NVMUPD fatal=nvm-not-found'
  exit 1
fi
default_version="$(nvm version default 2>/dev/null)"
if [[ -z "$default_version" || "$default_version" == "N/A" ]]; then
  printf '%s\\n' 'NVMUPD fatal=no-default'
  exit 1
fi
printf 'NVMUPD default=%s\\n' "$default_version"
other_versions="$(nvm ls --no-colors 2>/dev/null | grep -v 'N/A' | grep -v '\\->' | grep -oE 'v[0-9]+\\.[0-9]+\\.[0-9]+' | grep -v "^$default_version$" | sort -rV | uniq)"
printf '%s\\n' 'NVMUPD lane=default status=ok detail=dry-run'
for version in $other_versions; do
  printf 'NVMUPD lane=propagate:%s status=ok detail=dry-run\\n' "$version"
done
printf '%s\\n' 'NVMUPD lane=system status=ok detail=dry-run'
printf '%s\\n' 'NVMUPD lane=final status=ok detail=dry-run'
exit 0
"""
    dry_flag = "1" if dry_run else "0"
    return f"""set +e
source {shlex.quote(str(script))}
if ! command -v nvm >/dev/null 2>&1; then
  printf '%s\\n' 'NVMUPD fatal=nvm-not-found'
  exit 1
fi
default_version="$(nvm version default 2>/dev/null)"
if [[ -z "$default_version" || "$default_version" == "N/A" ]]; then
  printf '%s\\n' 'NVMUPD fatal=no-default'
  exit 1
fi
printf 'NVMUPD default=%s\\n' "$default_version"
other_versions="$(nvm ls --no-colors 2>/dev/null | \\
  grep -v 'N/A' | grep -v '\\->' | grep -oE 'v[0-9]+\\.[0-9]+\\.[0-9]+' | \\
  grep -v "^$default_version$" | sort -rV | uniq)"
if [[ "{dry_flag}" == 1 ]]; then
  printf '%s\\n' 'NVMUPD lane=default status=ok detail=dry-run'
  for version in $other_versions; do
    printf 'NVMUPD lane=propagate:%s status=ok detail=dry-run\\n' "$version"
  done
  printf '%s\\n' 'NVMUPD lane=system status=ok detail=dry-run'
  printf '%s\\n' 'NVMUPD lane=final status=ok detail=dry-run'
  exit 0
fi

if nvm use default >/dev/null 2>&1; then
  if command -v npm >/dev/null 2>&1 && npm update -g >/dev/null 2>&1; then
    printf '%s\\n' 'NVMUPD lane=default status=ok detail=updated'
  else
    printf '%s\\n' 'NVMUPD lane=default status=warn detail=npm-update-failed'
  fi
else
  printf '%s\\n' 'NVMUPD lane=default status=warn detail=nvm-use-failed'
fi
for version in $other_versions; do
  if ! nvm use "$version" >/dev/null 2>&1; then
    printf 'NVMUPD lane=propagate:%s status=warn detail=nvm-use-failed\\n' "$version"
    continue
  fi
  if nvm reinstall-packages default >/dev/null 2>&1; then
    printf 'NVMUPD lane=propagate:%s status=ok detail=reinstalled\\n' "$version"
  else
    printf 'NVMUPD lane=propagate:%s status=warn detail=reinstall-failed\\n' "$version"
  fi
done

system_npm=""
if command -v brew >/dev/null 2>&1; then
  brew_prefix="$(brew --prefix 2>/dev/null)"
  [[ -x "$brew_prefix/bin/npm" ]] && system_npm="$brew_prefix/bin/npm"
fi
if [[ -z "$system_npm" ]]; then
  for prefix in /opt/homebrew /usr/local; do
    if [[ -x "$prefix/bin/npm" ]]; then system_npm="$prefix/bin/npm"; break; fi
  done
fi
if [[ -n "$system_npm" ]]; then
  system_node="${{system_npm%/*}}/node"
  system_version="$($system_node --version 2>/dev/null || printf unknown)"
  if "$system_npm" update -g >/dev/null 2>&1; then
    printf 'NVMUPD lane=system status=ok detail=%s\\n' "$system_version"
  else
    printf 'NVMUPD lane=system status=warn detail=npm-update-failed\\n'
  fi
else
  printf '%s\\n' 'NVMUPD lane=system status=ok detail=none-found'
fi
if nvm use default >/dev/null 2>&1; then
  printf '%s\\n' 'NVMUPD lane=final status=ok detail=default-restored'
else
  printf '%s\\n' 'NVMUPD lane=final status=warn detail=default-restore-failed'
fi
"""


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


def update_globals(dry_run=False):
    script = _nvm_script()
    if script is None:
        logger.error(
            "nvm not found. Ensure NVM_DIR=%s and nvm.sh is sourced.",
            Path.home() / ".nvm",
        )
        return 1
    try:
        result = subprocess.run(
            ["bash", "-c", _compose_script(script, dry_run=dry_run)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        logger.warning("Could not run nvm update shell: %s", error)
        return 1
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    markers = re.findall(r"NVMUPD ([^\n]+)", output)
    for marker in markers:
        fields = dict(item.split("=", 1) for item in marker.split() if "=" in item)
        lane = fields.get("lane", "unknown")
        status = fields.get("status")
        detail = fields.get("detail", "")
        if status == "warn":
            logger.warning("nvm lane %s warning: %s", lane, detail)
        elif status == "ok":
            logger.info("nvm lane %s: %s", lane, detail)
    if "fatal=" in output:
        logger.error(
            "nvm update failed: %s",
            output.strip().split("fatal=", 1)[1].splitlines()[0],
        )
        return 1
    if result.returncode:
        logger.error("nvm update shell failed with exit code %s", result.returncode)
        return 1
    logger.info(
        "Global packages updated across all node versions.\n\nUpdate script complete!"
    )
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    cli_helpers.add_common_args(parser)
    args = parser.parse_args()
    raise SystemExit(update_globals(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
