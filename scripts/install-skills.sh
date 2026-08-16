#!/usr/bin/env bash
set -euo pipefail

_SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
LIB_DIR="$SCRIPT_DIR/lib"

source "$LIB_DIR/common.sh"
source "$LIB_DIR/common_args.sh"
export COMMON_USAGE="$0"
export COMMON_HELP_TEXT="Install the skills and lazyskills CLI tools."
parse_common_args "$@"
set -- "${COMMON_ARGS_REMAINING[@]}"

# install-skills.sh — Cross-platform installer for skills CLI tools.
# Installs:
#   - `skills` CLI (vercel-labs/skills) — skill fetching and management
#   - `lazyskills` (alvinunreal/lazyskills) — skill registry discovery
#
# Platform support:
#   macOS:   brew install skills; brew install --cask alvinunreal/tap/lazyskills
#   Linux:   npm install -g skills; curl -fsSL https://lazyskills.sh/install | sh
#   Windows: npm install -g skills; irm https://lazyskills.sh/install.ps1 | iex

OS_TYPE="$(uname -s)"

# --- skills CLI ---
info "Installing skills CLI..."
if command -v skills >/dev/null 2>&1; then
  ok "skills CLI already installed ($(skills --version 2>/dev/null || echo 'unknown'))"
else
  case "$OS_TYPE" in
  Darwin | Linux)
    if command -v brew >/dev/null 2>&1; then
      brew install skills || warn "brew install skills failed"
    elif command -v npm >/dev/null 2>&1; then
      npm install -g skills || warn "npm install -g skills failed"
    else
      warn "Neither brew nor npm found — cannot install skills CLI"
    fi
    ;;
  MINGW* | MSYS* | CYGWIN* | Windows*)
    if command -v npm >/dev/null 2>&1; then
      npm install -g skills || warn "npm install -g skills failed"
    else
      warn "npm not found — install Node.js first (winget install OpenJS.NodeJS)"
    fi
    ;;
  *)
    warn "Unknown OS: $OS_TYPE — skipping skills CLI install"
    ;;
  esac
fi

# --- lazyskills ---
info "Installing lazyskills CLI..."
if command -v lazyskills >/dev/null 2>&1; then
  ok "lazyskills CLI already installed"
else
  case "$OS_TYPE" in
  Darwin)
    if command -v brew >/dev/null 2>&1; then
      brew tap alvinunreal/tap 2>/dev/null || true
      brew install --cask lazyskills || warn "brew install --cask lazyskills failed"
    else
      warn "brew not found — cannot install lazyskills on macOS"
    fi
    ;;
  Linux)
    if command -v curl >/dev/null 2>&1; then
      curl -fsSL https://lazyskills.sh/install | sh || warn "lazyskills curl install failed"
    else
      warn "curl not found — cannot install lazyskills"
    fi
    ;;
  MINGW* | MSYS* | CYGWIN* | Windows*)
    if command -v powershell >/dev/null 2>&1 || command -v pwsh >/dev/null 2>&1; then
      PWSH_CMD="$(command -v pwsh 2>/dev/null || command -v powershell 2>/dev/null)"
      "$PWSH_CMD" -Command "irm https://lazyskills.sh/install.ps1 | iex" || warn "lazyskills PowerShell install failed"
    else
      warn "PowerShell not found — cannot install lazyskills on Windows"
    fi
    ;;
  *)
    warn "Unknown OS: $OS_TYPE — skipping lazyskills install"
    ;;
  esac
fi

ok "Skills CLI tools install complete!"
