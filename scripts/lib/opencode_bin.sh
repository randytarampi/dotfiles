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
    case "$(uname -s 2>/dev/null || true)" in
    MINGW* | MSYS* | CYGWIN*)
      # The verified Windows channel is the official NSIS installer; its
      # CLI lands in the app install dirs (no ~/.opencode/bin on Windows).
      # ${LOCALAPPDATA:-} guards unset vars under set -u.
      local win_root
      for win_root in "${LOCALAPPDATA:-}/Programs/opencode" "${LOCALAPPDATA:-}/Programs/OpenCode" "${PROGRAMFILES:-}/OpenCode"; do
        if [[ -x "$win_root/opencode.exe" ]]; then
          resolved="$win_root/opencode.exe"
          break
        fi
      done
      ;;
    esac
  fi
  if [[ -z "$resolved" ]]; then
    resolved="$(command -v opencode 2>/dev/null || true)"
  fi
  printf '%s\n' "$resolved"
}

# 0 when the given --version output shows exactly one stable v2 X.Y.Z token
# (prerelease/build suffixes, missing output, v1 or ambiguous output fail).
opencode_bin_output_is_stable_v2() {
  local output="$1"
  python3 - "$output" <<'PY'
import re, sys
versions = set(re.findall(r"(?<![A-Za-z0-9.+-])v?(\d+\.\d+\.\d+)(?![A-Za-z0-9.+-])", sys.argv[1]))
raise SystemExit(0 if len(versions) == 1 and versions.pop().split('.', 1)[0] == '2' else 1)
PY
}
