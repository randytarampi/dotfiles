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
  echo "Would install @getpaseo/cli@0.15.0 globally; no changes made"
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
# Stable pin for this pilot. No implicit beta upgrades.
npm install --global --no-audit --no-fund '@getpaseo/cli@0.15.0'
command -v paseo >/dev/null || {
  echo 'Paseo CLI not found after installation' >&2
  exit 1
}
version="$(paseo --version)"
if [[ ! "$version" =~ ^([Pp]aseo[[:space:]]+)?v?0[.]15[.]0$ ]]; then
  echo 'Installed Paseo CLI does not report pinned v0.15.0' >&2
  exit 1
fi
printf '%s\n' "Paseo CLI verified: $version"
printf '%s\n' 'Set daemon password and explicitly disable relay before tailnet exposure.'
