#!/usr/bin/env bash
set -euo pipefail
# Explicit owner action only. Not invoked by deploy or chezmoi.
case "${1:-}" in
--help | -h)
  echo "Usage: scripts/install-paseo.sh [--help|--dry-run]"
  exit 0
  ;;
--dry-run)
  if [[ "$#" -ne 1 ]]; then
    echo "Unexpected arguments" >&2
    exit 2
  fi
  echo "Would install @getpaseo/cli@latest globally; no changes made"
  exit 0
  ;;
"") ;;
*)
  echo "Unexpected argument: $1" >&2
  exit 2
  ;;
esac
if [[ "$#" -gt 1 ]]; then
  echo "Unexpected arguments" >&2
  exit 2
fi
if [[ "${DOTFILES_RUN_PASEO_SETUP:-0}" != "1" ]]; then
  echo 'Refusing Paseo install: set DOTFILES_RUN_PASEO_SETUP=1' >&2
  exit 1
fi
if ! command -v npm >/dev/null; then
  echo 'Refusing Paseo install: npm not found' >&2
  exit 1
fi
# Explicit owner-run stable channel; the owner intentionally follows @latest.
# Refuse an unparsable or prerelease CLI version rather than accepting any output.
npm install --global --no-audit --no-fund '@getpaseo/cli@latest'
command -v paseo >/dev/null || {
  echo 'Paseo CLI not found after installation' >&2
  exit 1
}
# npm's global prefix identifies the installed executable. Reject an older
# shadowing binary earlier on PATH instead of reporting a false success.
npm_paseo="$(npm prefix -g)/bin/paseo"
resolved_paseo="$(command -v paseo)"
if [[ ! -x "$npm_paseo" || "$resolved_paseo" != "$npm_paseo" ]]; then
  echo "Paseo CLI on PATH does not match npm global executable at $npm_paseo" >&2
  exit 1
fi
version="$("$npm_paseo" --version)"
if [[ ! "$version" =~ ^([Pp]aseo[[:space:]]+)?v?[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo 'Installed Paseo CLI did not report a stable semantic version' >&2
  exit 1
fi
printf '%s\n' "Paseo CLI verified: $version"
printf '%s\n' 'Set daemon password and explicitly disable relay before tailnet exposure.'
