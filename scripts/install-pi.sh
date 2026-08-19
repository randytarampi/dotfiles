#!/usr/bin/env bash
set -euo pipefail

_SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
LIB_DIR="$SCRIPT_DIR/lib"
source "$LIB_DIR/common.sh"
source "$LIB_DIR/common_args.sh"
export COMMON_USAGE="$0"
export COMMON_HELP_TEXT="Install the Pi terminal coding agent."
export COMMON_STRICT=1
parse_common_args "$@"

if [[ "${DOTFILES_RUN_PI_SETUP:-0}" != "1" ]]; then
  info "DOTFILES_RUN_PI_SETUP='${DOTFILES_RUN_PI_SETUP:-0}' — skipping Pi install"
  exit 0
fi

if command -v pi >/dev/null 2>&1; then
  ok "Pi already installed: $(pi --version 2>/dev/null || true)"
  exit 0
fi

if [[ "$COMMON_DRY_RUN" == "1" ]]; then
  info "[DRY RUN] Would install Pi via curl installer or npm fallback"
  exit 0
fi

if [[ "$(uname -s)" == "Darwin" || "$(uname -s)" == "Linux" ]]; then
  curl -fsSL https://pi.dev/install.sh | sh || warn "Pi installer failed; trying npm fallback"
fi

if ! command -v pi >/dev/null 2>&1; then
  npm install -g --ignore-scripts @earendil-works/pi-coding-agent
fi

command -v pi >/dev/null 2>&1 || die "Pi installation completed but pi is not on PATH"
ok "Pi installed: $(pi --version 2>/dev/null || true)"
