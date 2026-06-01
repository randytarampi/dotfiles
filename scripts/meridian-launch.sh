#!/usr/bin/env bash
set -euo pipefail
# meridian-launch.sh — Launch wrapper for meridian that ensures proper Keychain access
#
# This script is called by launchd (com.meridian.proxy.plist) to start meridian.
# Cross-arch: tries brew prefix, HOMEBREW_PREFIX, nvm, PATH, then uname fallback.
#
# Meridian authenticates via the Claude Code SDK's own OAuth flow — it does NOT
# use ANTHROPIC_API_KEY. Those env vars are only for OpenCode's provider config.

# Strip env vars that confuse the Claude CLI's auth detection.
# These are set by ~/.env for OpenCode but must NOT leak into meridian.
unset ANTHROPIC_API_KEY
unset ANTHROPIC_BASE_URL

# Resolve meridian binary: brew prefix → HOMEBREW_PREFIX → nvm → PATH → uname fallback
_MERIDIAN=""
if command -v brew >/dev/null 2>&1; then
  _MERIDIAN="$(brew --prefix 2>/dev/null)/bin/meridian"
  [[ ! -x "$_MERIDIAN" ]] && _MERIDIAN=""
fi
if [[ -z "$_MERIDIAN" && -n "${HOMEBREW_PREFIX:-}" && -x "${HOMEBREW_PREFIX}/bin/meridian" ]]; then
  _MERIDIAN="${HOMEBREW_PREFIX}/bin/meridian"
fi
if [[ -z "$_MERIDIAN" ]]; then
  # Try nvm: scan installed versions for meridian (newest first)
  _nvm_dir="${NVM_DIR:-$HOME/.nvm}"
  if [[ -d "$_nvm_dir/versions/node" ]]; then
    for _node_dir in $(ls -1d "$_nvm_dir/versions/node/"v[0-9]* 2>/dev/null | sort -rV); do
      if [[ -x "$_node_dir/bin/meridian" ]]; then
        _MERIDIAN="$_node_dir/bin/meridian"
        break
      fi
    done
  fi
fi
if [[ -z "$_MERIDIAN" ]]; then
  _MERIDIAN="$(command -v meridian 2>/dev/null || true)"
fi
if [[ -z "$_MERIDIAN" ]]; then
  # Fallback: try Intel then ARM Homebrew prefixes directly
  for _prefix in /usr/local /opt/homebrew; do
    if [[ -x "$_prefix/bin/meridian" ]]; then
      _MERIDIAN="$_prefix/bin/meridian"
      break
    fi
  done
fi

exec "${_MERIDIAN:-meridian}"
