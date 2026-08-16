#!/usr/bin/env bash
set -euo pipefail

# ─── install-npm-brewfile-packages.sh ─────────────────────────────────────────
#
# Reconciles `npm "..."` entries from a Brewfile into the active nvm node.
#
# `brew bundle` installs `npm "..."` entries into Homebrew's own node
# ($(brew --prefix)/opt/node/lib/node_modules/), NOT the nvm node on PATH. This
# means user-facing CLIs declared as `npm "..."` in Brewfiles are installed but
# not reachable via `which <cmd>`. This script re-installs each package into the
# active node so those CLIs land on PATH.
#
# Usage: install-npm-brewfile-packages.sh <Brewfile-path>
# Idempotent: safe to re-run (npm install -g is idempotent).
# ───────────────────────────────────────────────────────────────────────────────

_SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
LIB_DIR="$SCRIPT_DIR/lib"

source "$LIB_DIR/common.sh"
source "$LIB_DIR/common_args.sh"

export COMMON_USAGE="$0 <Brewfile-path> [options]"
export COMMON_HELP_TEXT="Reinstall npm entries from a Brewfile into the active nvm node."
parse_common_args "$@"
set -- "${COMMON_ARGS_REMAINING[@]}"

BREWFILE="${1:-}"
if [[ -z "$BREWFILE" ]]; then
  die "Usage: $0 <Brewfile-path>"
fi

if [[ ! -f "$BREWFILE" ]]; then
  die "Brewfile not found: $BREWFILE"
fi

# Brew-prefix guard: if npm resolves to brew's node, skip (nvm not active).
# Reinstalling here would just target brew's node again — a no-op.
NPM_BIN="$(command -v npm 2>/dev/null || echo "")"
if [[ -z "$NPM_BIN" ]]; then
  warn "npm not found on PATH — skipping npm package reconciliation"
  exit 0
fi

BREW_PREFIX="$(brew --prefix 2>/dev/null || echo "")"
if [[ -n "$BREW_PREFIX" && "$NPM_BIN" == "$BREW_PREFIX"* ]]; then
  warn "npm resolves to Homebrew's node ($NPM_BIN) — nvm not active, skipping reconciliation to avoid reinstalling into brew's node"
  exit 0
fi

# Extract npm "pkg" entries from Brewfile
info "Reconciling npm packages from $BREWFILE into active node ($(node -v 2>/dev/null || echo "unknown"))..."

PACKAGES="$(grep -E '^[[:space:]]*npm[[:space:]]+"' "$BREWFILE" | sed -E 's/^[[:space:]]*npm[[:space:]]+"([^"]+)".*$/\1/' || true)"

if [[ -z "$PACKAGES" ]]; then
  ok "No npm entries found in $BREWFILE"
  exit 0
fi

INSTALLED_COUNT=0
FAILED_COUNT=0

while IFS= read -r pkg; do
  [[ -z "$pkg" ]] && continue
  info "Installing $pkg..."
  if npm install -g "$pkg@latest" >/dev/null 2>&1; then
    ok "$pkg installed"
    INSTALLED_COUNT=$((INSTALLED_COUNT + 1))
  else
    warn "Failed to install $pkg"
    FAILED_COUNT=$((FAILED_COUNT + 1))
  fi
done <<<"$PACKAGES"

# Summary in a single log call (per repo logging conventions)
info "Reconciliation complete for $BREWFILE:
  • Installed/updated: $INSTALLED_COUNT
  • Failed: $FAILED_COUNT"
