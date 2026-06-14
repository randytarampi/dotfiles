#!/usr/bin/env bash
set -euo pipefail

_SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"

# ─── install-smallcode.sh ─────────────────────────────────────────────────────
#
# Installs SmallCode CLI. Run once per machine.
# Does NOT write any configuration files — use configure-smallcode.py for that.
#
# What this installs:
#   1. smallcode CLI
# ───────────────────────────────────────────────────────────────────────────────

source "$SCRIPT_DIR/lib/common.sh"

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------
SMALLCODE_SETUP="${DOTFILES_RUN_SMALLCODE_SETUP:-0}"
if [[ "$SMALLCODE_SETUP" != "1" ]]; then
  info "DOTFILES_RUN_SMALLCODE_SETUP='${SMALLCODE_SETUP}' — skipping SmallCode install"
  exit 0
fi

info "Checking prerequisites..."

HAS_NPM=0
HAS_BUN=0
if command -v npm &>/dev/null; then
  HAS_NPM=1
fi
if command -v bun &>/dev/null; then
  HAS_BUN=1
fi

if [[ "$HAS_NPM" -eq 0 && "$HAS_BUN" -eq 0 ]]; then
  die "Need 'npm' or 'bun'. Install Node.js or Bun first: https://nodejs.org or https://bun.sh"
fi

if command -v smallcode &>/dev/null; then
  warn "smallcode is already installed; continuing to ensure it is up to date"
fi

# ---------------------------------------------------------------------------
# 1. Install SmallCode CLI
# ---------------------------------------------------------------------------
if [[ "$HAS_NPM" -eq 1 ]]; then
  info "Installing SmallCode CLI globally via npm..."
  if npm install -g smallcode; then
    ok "SmallCode CLI installed via npm"
  else
    die "SmallCode CLI install failed via npm"
  fi
else
  info "Installing SmallCode CLI globally via bun..."
  if bun add -g smallcode; then
    ok "SmallCode CLI installed via bun"
  else
    die "SmallCode CLI install failed via bun"
  fi
fi

hash -r 2>/dev/null || true

# ---------------------------------------------------------------------------
# 2. Verify installation
# ---------------------------------------------------------------------------
info "Verifying SmallCode installation..."
if SMALLCODE_VERSION="$(smallcode --version 2>/dev/null)"; then
  ok "SmallCode verified: ${SMALLCODE_VERSION}"
else
  die "SmallCode verification failed after install"
fi

# ---------------------------------------------------------------------------
# Done!
# ---------------------------------------------------------------------------
info "SmallCode installed!

Next steps:
  1. Configure:     configure-smallcode.py --preset <tier>
  2. Set up .env:   Edit ~/.config/smallcode/.env with your model and base URL
  3. Run:           smallcode

Install script complete!"
