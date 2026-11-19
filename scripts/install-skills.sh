#!/usr/bin/env bash
set -euo pipefail

_SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
LIB_DIR="$SCRIPT_DIR/lib"

source "$LIB_DIR/common.sh"
source "$LIB_DIR/common_args.sh"
export COMMON_USAGE="$0"
export COMMON_HELP_TEXT="Install the skills CLI."
export COMMON_STRICT=1
parse_common_args "$@"
set -- ${COMMON_ARGS_REMAINING[@]+"${COMMON_ARGS_REMAINING[@]}"}
if [[ "$COMMON_NO_BACKUP" == "1" ]]; then
  printf '%s\n' "Error: --no-backup is not supported by this script" >&2
  exit 2
fi

# install-skills.sh — Cross-platform installer for the skills CLI.
# Installs `skills` (vercel-labs/skills) — skill fetching and management.
# LazySkills was retired from repo management in 2026-11; install it manually
# if you want its TUI, but it is no longer a managed dependency.
#
# Platform support:
#   macOS:   brew install skills
#   Linux:   npm install -g skills
#   Windows: npm install -g skills

OS_TYPE="$(uname -s)"

# --- skills CLI ---
info "Installing skills CLI..."
if command -v skills >/dev/null 2>&1; then
  ok "skills CLI already installed ($(skills --version 2>/dev/null || echo 'unknown'))"
else
  case "$OS_TYPE" in
  Darwin | Linux)
    if command -v brew >/dev/null 2>&1; then
      if [[ "$COMMON_DRY_RUN" == "1" ]]; then
        info "[DRY RUN] Would run: brew install skills"
      else
        brew install skills || warn "brew install skills failed"
      fi
    elif command -v npm >/dev/null 2>&1; then
      if [[ "$COMMON_DRY_RUN" == "1" ]]; then
        info "[DRY RUN] Would run: npm install -g skills"
      else
        npm install -g skills || warn "npm install -g skills failed"
      fi
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

ok "Skills CLI tools install complete!"
