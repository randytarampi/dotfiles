#!/usr/bin/env bash
set -euo pipefail

_SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"

source "$SCRIPT_DIR/lib/common.sh"

export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"

# Get current default version (source for --reinstall-packages-from)
DEFAULT_VERSION=$(nvm version default 2>/dev/null | sed 's/^v//')
info "Default node version: v$DEFAULT_VERSION"

# Get all installed LTS versions, sort numerically
LTS_VERSIONS=$(nvm ls --no-colors | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+' | sed 's/^v//' | cut -d. -f1 | sort -unr)

info "Reinstalling all LTS node versions with latest npm and global packages from v$DEFAULT_VERSION..."
for ver in $LTS_VERSIONS; do
  info "Reinstalling node $ver..."
  nvm install "$ver" --reinstall-packages-from="$DEFAULT_VERSION" --latest-npm 2>&1 || warn "node $ver reinstall failed"
done

ok "All LTS versions reinstalled."
