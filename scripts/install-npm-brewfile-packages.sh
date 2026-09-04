#!/usr/bin/env bash
set -euo pipefail

# ─── install-npm-brewfile-packages.sh ─────────────────────────────────────────
#
# Reconciles `npm "..."` entries from a Brewfile into the active node.
#
# `brew bundle` installs `npm "..."` entries into Homebrew's own node
# ($(brew --prefix)/opt/node/lib/node_modules/), NOT the nvm node on PATH. This
# means user-facing CLIs declared as `npm "..."` in Brewfiles are installed but
# not reachable via `which <cmd>` when an nvm node is active. This script
# re-installs each package into whichever node is active (nvm or Homebrew's
# system node) so those CLIs land on PATH — Homebrew's node bin is always on
# PATH, so installing there is equally reachable when `nvm default -> system`.
#
# If `corepack` is among the reconciled packages, run `corepack enable` after
# installation: installing corepack alone does not create the yarn/yarnpkg/pnpm
# shims; only `corepack enable` does.
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
export COMMON_STRICT=1
parse_common_args "$@"
set -- ${COMMON_ARGS_REMAINING[@]+"${COMMON_ARGS_REMAINING[@]}"}
if [[ "$COMMON_NO_BACKUP" == "1" ]]; then
  printf '%s\n' "Error: --no-backup is not supported by this script" >&2
  exit 2
fi

BREWFILE="${1:-}"
if [[ -z "$BREWFILE" ]]; then
  die "Usage: $0 <Brewfile-path>"
fi

if [[ ! -f "$BREWFILE" ]]; then
  die "Brewfile not found: $BREWFILE"
fi

# npm guard: whichever node npm resolves to, its bin is on PATH, so installing
# there makes the CLIs reachable (nvm node bin when nvm is active, or
# /opt/homebrew/bin when Homebrew's system node is active).
NPM_BIN="$(command -v npm 2>/dev/null || echo "")"
if [[ -z "$NPM_BIN" ]]; then
  warn "npm not found on PATH — skipping npm package reconciliation"
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
RECONCILED_COREPACK=0

while IFS= read -r pkg; do
  [[ -z "$pkg" ]] && continue
  if [[ "$COMMON_DRY_RUN" == "1" ]]; then
    info "[DRY RUN] Would install $pkg@latest globally"
    if [[ "$pkg" == "corepack" ]]; then
      info "[DRY RUN] Would run corepack enable"
    fi
    continue
  fi
  info "Installing $pkg..."
  if npm install -g "$pkg@latest" >/dev/null 2>&1; then
    ok "$pkg installed"
    INSTALLED_COUNT=$((INSTALLED_COUNT + 1))
    if [[ "$pkg" == "corepack" ]]; then
      RECONCILED_COREPACK=1
    fi
  else
    warn "Failed to install $pkg"
    FAILED_COUNT=$((FAILED_COUNT + 1))
  fi
done <<<"$PACKAGES"

# Materialize corepack shims (yarn/yarnpkg/pnpm) when corepack was reconciled.
# Installing corepack alone does not create the shims; corepack enable does.
# Idempotent and network-free; shims land in the active node's bin (on PATH).
if [[ "$COMMON_DRY_RUN" != "1" && "$RECONCILED_COREPACK" == "1" ]]; then
  if command -v corepack >/dev/null 2>&1; then
    info "Enabling corepack shims (yarn/yarnpkg/pnpm)..."
    if corepack enable >/dev/null 2>&1; then
      ok "corepack shims enabled"
    else
      warn "Failed to run corepack enable — yarn/pnpm shims may be missing"
    fi
  else
    warn "corepack not resolvable after install — skipping corepack enable"
  fi
fi

# Summary in a single log call (per repo logging conventions)
info "Reconciliation complete for $BREWFILE:
  • Installed/updated: $INSTALLED_COUNT
  • Failed: $FAILED_COUNT"
