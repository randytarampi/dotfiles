#!/usr/bin/env bash
set -euo pipefail

HOME_BIN="$HOME/.opencode/bin/opencode"

if [[ "${DOTFILES_RUN_OPENCODE_TOOLS_SETUP:-0}" != "1" ]]; then
  printf '[INFO] DOTFILES_RUN_OPENCODE_TOOLS_SETUP is off; skipping OpenCode host setup.\n' >&2
  exit 0
fi

probe_v2() {
  local selected output
  if [[ -x "$HOME_BIN" ]]; then selected="$HOME_BIN"; else selected="$(command -v opencode || true)"; fi
  [[ -n "$selected" && -x "$selected" ]] || return 1
  output="$("$selected" --version 2>&1)" || return 1
  if ! python3 - "$output" <<'PY'
import re, sys
versions = set(re.findall(r"(?<![A-Za-z0-9.+-])v?(\d+\.\d+\.\d+)(?![A-Za-z0-9.+-])", sys.argv[1]))
raise SystemExit(0 if len(versions) == 1 and versions.pop().split('.', 1)[0] == '2' else 1)
PY
  then return 1; fi
  printf '[INFO] OpenCode v2 verified at %s (%s).\n' "$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$selected")" "$output" >&2
}

if probe_v2; then
  printf '[INFO] Existing OpenCode v2 install is current; verification-only pass.\n' >&2
  exit 0
fi

case "$(uname -s)" in
Darwin | Linux)
  curl -fsSL https://opencode.ai/v2/install | bash -s --
  ;;
MINGW* | MSYS* | CYGWIN*)
  arch="$(uname -m)"
  case "$arch" in
  AMD64 | x86_64) url="https://opencode.ai/download/latest/windows-x64-nsis" ;;
  ARM64 | aarch64) url="https://opencode.ai/download/latest/windows-aarch64-nsis" ;;
  *)
    printf '[ERROR] Unsupported Windows architecture: %s\n' "$arch" >&2
    exit 1
    ;;
  esac
  # shellcheck disable=SC2016  # single quotes protect the PowerShell command text.
  cli_path="$(OPENCODE_INSTALL_URL="$url" powershell.exe -NoProfile -NonInteractive -Command '$ErrorActionPreference="Stop"; $p=Join-Path $env:TEMP "opencode-v2-setup.exe"; Invoke-WebRequest -Uri $env:OPENCODE_INSTALL_URL -OutFile $p; Start-Process -FilePath $p -ArgumentList "/S" -Wait -NoNewWindow; $candidates=@((Join-Path $env:LOCALAPPDATA "Programs\opencode\opencode.exe"),(Join-Path $env:LOCALAPPDATA "Programs\OpenCode\opencode.exe"),(Join-Path $env:ProgramFiles "OpenCode\opencode.exe")); $found=$candidates | Where-Object { Test-Path $_ } | Select-Object -First 1; if ($found) { Write-Output $found } else { exit 3 }')" || {
    printf '[ERROR] OpenCode Windows NSIS installer ran, but no opencode.exe was found in the known install directories; not claiming success.\n' >&2
    exit 1
  }
  cli_path="${cli_path%%$'\r'*}"
  windows_version="$("$cli_path" --version 2>&1)" || {
    printf '[ERROR] OpenCode Windows CLI at %s failed its --version probe.\n' "$cli_path" >&2
    exit 1
  }
  if ! printf '%s' "$windows_version" | grep -Eq '(^|[^0-9a-zA-Z.+-])v?2\.[0-9]+\.[0-9]+([^0-9a-zA-Z.+-]|$)'; then
    printf '[ERROR] OpenCode Windows CLI probe did not report a stable v2 release: %s\n' "$windows_version" >&2
    exit 1
  fi
  printf '[INFO] OpenCode Windows CLI verified at %s (%s); per-machine acceptance for Windows remains owner-only.\n' "$cli_path" "$windows_version" >&2
  exit 0
  ;;
*)
  printf '[ERROR] Unsupported host OS: %s\n' "$(uname -s)" >&2
  exit 1
  ;;
esac

if ! probe_v2; then
  printf '[ERROR] OpenCode installer completed but a successful v2 CLI probe could not be proved.\n' >&2
  exit 1
fi

if command -v brew >/dev/null 2>&1 && brew list anomalyco/tap/opencode >/dev/null 2>&1; then
  printf '[INFO] Removing obsolete v1 Homebrew formula after verified v2 install.\n' >&2
  brew uninstall anomalyco/tap/opencode || true
fi
