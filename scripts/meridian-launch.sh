#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/common_args.sh"
export COMMON_USAGE="$0"
export COMMON_HELP_TEXT="Launch the Meridian proxy service."
parse_common_args "$@"
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

_MERIDIAN="$(PYTHONPATH="$SCRIPT_DIR/lib" python3 -m meridian_path --binary 2>/dev/null || true)"

exec "${_MERIDIAN:-meridian}"
