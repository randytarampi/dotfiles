#!/usr/bin/env bash

# Canonical managed OpenCode executable resolver.
#
# Precedence: $OPENCODE_MANAGED_BIN (explicit override) > the managed install
# directory ($HOME/.opencode/bin/opencode, written by the official v2
# installer) > PATH fallback. Every OpenCode-executing consumer (host
# installer, plugin installer, Meridian setup, generator subprocesses,
# doctor, service templates) must go through this resolver so a managed v2
# binary is never shadowed by a stale PATH v1 in non-login contexts
# (chezmoi children, launchd jobs, CI steps) where dot_dotfiles/shell/paths.sh
# has not been sourced.
opencode_managed_bin() {
  local resolved="${OPENCODE_MANAGED_BIN:-}"
  if [[ -z "$resolved" && -x "$HOME/.opencode/bin/opencode" ]]; then
    resolved="$HOME/.opencode/bin/opencode"
  fi
  if [[ -z "$resolved" ]]; then
    resolved="$(command -v opencode 2>/dev/null || true)"
  fi
  printf '%s\n' "$resolved"
}
