#!/usr/bin/env bash
set -euo pipefail

HOME_BIN="$HOME/.opencode/bin/opencode"
MODE="auto"
PINNED_VERSION=""

usage() {
  cat >&2 <<'USAGE'
Usage: install-opencode-v2.sh [--install-latest | --verify | --version X.Y.Z]
  (default) install-latest only when no verified v2 host is installed; otherwise
  a verification-only pass (the `opencode upgrade` update lane refreshes latest
  on its own schedule and re-proves plugin convergence).
  --install-latest  always run the official installer plus strict post-verify;
                    never skips because a major-2 binary happens to exist.
  --verify          read-only: prove a stable v2 CLI or exit 1; never installs.
  --version X.Y.Z   pinned qualification stage (Meridian): installs that exact
                    release when different; never accepts a prerelease or a
                    merely-different version.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
  --install-latest) MODE="install-latest" ;;
  --verify) MODE="verify" ;;
  --version)
    shift
    if [[ $# -eq 0 || ! "${1:-}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
      printf '[ERROR] --version requires an exact stable X.Y.Z argument.\n' >&2
      usage
      exit 2
    fi
    MODE="pinned"
    PINNED_VERSION="$1"
    ;;
  --help | -h)
    usage
    exit 0
    ;;
  *)
    printf '[ERROR] Unknown argument: %s\n' "$1" >&2
    usage
    exit 2
    ;;
  esac
  shift
done

if [[ "${DOTFILES_RUN_OPENCODE_TOOLS_SETUP:-0}" != "1" ]]; then
  printf '[INFO] DOTFILES_RUN_OPENCODE_TOOLS_SETUP is off; skipping OpenCode host setup.\n' >&2
  exit 0
fi

probe_v2() {
  # probe_v2 [expected_version] — expected present only in pinned mode.
  local selected output expected="${2:-}"
  if [[ -x "$HOME_BIN" ]]; then selected="$HOME_BIN"; else selected="$(command -v opencode || true)"; fi
  [[ -n "$selected" && -x "$selected" ]] || return 1
  output="$("$selected" --version 2>&1)" || return 1
  local pyargs=(- "$output")
  [[ -n "$expected" ]] && pyargs+=("$expected")
  if ! python3 "${pyargs[@]}" <<'PY'
import re, sys
# argv after the script placeholder: [output, (expected?)] — positional,
# never index-counted from the leading dash.
args = sys.argv[1:]
versions = set(re.findall(r"(?<![A-Za-z0-9.+-])v?(\d+\.\d+\.\d+)(?![A-Za-z0-9.+-])", args[0]))
if len(versions) != 1:
    raise SystemExit(1)
version = versions.pop()
if version.split('.', 1)[0] != '2':
    raise SystemExit(1)
if len(args) > 1 and version != args[1]:
    raise SystemExit(1)
raise SystemExit(0)
PY
  then return 1; fi
  printf '[INFO] OpenCode v2 verified at %s (%s).\n' "$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$selected")" "$output" >&2
}

case "$MODE" in
verify)
  if probe_v2; then exit 0; fi
  printf '[ERROR] --verify: no stable OpenCode v2 CLI could be proved.\n' >&2
  exit 1
  ;;
pinned)
  if probe_v2 "" "$PINNED_VERSION"; then
    printf '[INFO] OpenCode %s already installed (pinned qualification stage).\n' "$PINNED_VERSION" >&2
    exit 0
  fi
  ;;
*)
  if [[ "$MODE" == "auto" ]] && probe_v2; then
    printf '[INFO] Existing OpenCode v2 install is current; verification-only pass. Latest-stable refresh belongs to the update lane (opencode upgrade + plugin re-check).\n' >&2
    exit 0
  fi
  ;;
esac

case "$(uname -s)" in
Darwin | Linux)
  curl -fsSL https://opencode.ai/v2/install | bash -s -- ${PINNED_VERSION:+--version "$PINNED_VERSION"}
  ;;
MINGW* | MSYS* | CYGWIN*)
  if [[ "$MODE" == "pinned" ]]; then
    printf '[ERROR] Pinned-version installs are not supported through the Windows NSIS channel (no versioned silent install argument is verified); use --install-latest there.\n' >&2
    exit 2
  fi
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

if ! probe_v2 "" "$PINNED_VERSION"; then
  if [[ -n "$PINNED_VERSION" ]]; then
    printf '[ERROR] Installer ran but the installed v2 CLI does not verify as the pinned version %s.\n' "$PINNED_VERSION" >&2
  else
    printf '[ERROR] OpenCode installer completed but a successful v2 CLI probe could not be proved.\n' >&2
  fi
  exit 1
fi

if command -v brew >/dev/null 2>&1 && brew list anomalyco/tap/opencode >/dev/null 2>&1; then
  printf '[INFO] Removing obsolete v1 Homebrew formula after verified v2 install.\n' >&2
  brew uninstall anomalyco/tap/opencode || true
fi
