#!/usr/bin/env bash
set -euo pipefail

_SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
LIB_DIR="$SCRIPT_DIR/lib"

# shellcheck disable=SC1091
source "$LIB_DIR/common.sh"
source "$LIB_DIR/common_args.sh"
export COMMON_USAGE="$0"
export COMMON_HELP_TEXT="Install the Plannotator paste service."
parse_common_args "$@"
set -- ${COMMON_ARGS_REMAINING[@]+"${COMMON_ARGS_REMAINING[@]}"}
# shellcheck disable=SC1091
source "$LIB_DIR/env.sh"

load_env || warn "\$HOME/.env not found, skipping env load"

if [[ "${DOTFILES_RUN_PLANNOTATOR_PASTE_SETUP:-${DOTFILES_RUN_CADDY_SETUP:-0}}" != "1" ]]; then
  info "DOTFILES_RUN_PLANNOTATOR_PASTE_SETUP='${DOTFILES_RUN_PLANNOTATOR_PASTE_SETUP:-${DOTFILES_RUN_CADDY_SETUP:-0}}' — skipping Plannotator paste install"
  exit 0
fi

# Install binary to brew prefix if available, else ~/.local/bin
if command -v brew >/dev/null 2>&1; then
  BREW_PREFIX="$(brew --prefix)"
  BIN_PATH="$BREW_PREFIX/bin/plannotator-paste"
else
  BIN_PATH="${HOME}/.local/bin/plannotator-paste"
  mkdir -p "$(dirname "$BIN_PATH")"
fi
DATA_DIR="${PASTE_DATA_DIR:-$HOME/.plannotator/pastes}"
PORTAL_DIR="$HOME/.plannotator/portal"
BUILD_DIR="/tmp/plannotator-build"

PRESENT_BIN="$(command -v plannotator-paste 2>/dev/null || true)"
if [[ -n "$PRESENT_BIN" ]]; then
  ok "Plannotator paste already installed at ${PRESENT_BIN}"
elif [[ -x "$BIN_PATH" ]]; then
  ok "Plannotator paste already installed at ${BIN_PATH}"
else
  case "$(uname -s)/$(uname -m)" in
  Darwin/arm64) ARCH="darwin-arm64" ;;
  Darwin/x86_64) ARCH="darwin-x64" ;;
  Linux/arm64 | Linux/aarch64) ARCH="linux-arm64" ;;
  Linux/x86_64) ARCH="linux-x64" ;;
  MINGW*/arm64 | MSYS*/arm64 | CYGWIN*/arm64) ARCH="win32-arm64" ;;
  MINGW*/x86_64 | MSYS*/x86_64 | CYGWIN*/x86_64) ARCH="win32-x64" ;;
  *) die "Unsupported platform: $(uname -s)/$(uname -m)" ;;
  esac

  TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/plannotator-paste.XXXXXX")"
  trap 'rm -rf "$TMP_DIR"' EXIT

  info "Installing Plannotator paste binary for ${ARCH}..."
  # Intentionally unpinned — always install latest Plannotator release (matches Junie CLI install pattern)
  DOWNLOAD_URL="https://github.com/backnotprop/plannotator/releases/latest/download/plannotator-paste-${ARCH}"
  if [[ "$ARCH" == win32-* ]]; then
    DOWNLOAD_URL="${DOWNLOAD_URL}.exe"
    BIN_PATH="${BIN_PATH}.exe"
  fi
  if ! curl -fsSL "$DOWNLOAD_URL" -o "$TMP_DIR/plannotator-paste"; then
    die "Plannotator paste install failed (URL: ${DOWNLOAD_URL})"
  fi

  if [[ ! -s "$TMP_DIR/plannotator-paste" ]]; then
    die "Plannotator paste binary not found after download"
  fi

  cp "$TMP_DIR/plannotator-paste" "$BIN_PATH"
  if [[ "$ARCH" != win32-* ]]; then
    chmod 755 "$BIN_PATH"
  fi
  ok "Plannotator paste installed to ${BIN_PATH}"
fi

mkdir -p "$DATA_DIR"
mkdir -p "$(dirname "$PORTAL_DIR")"

if command -v bun >/dev/null 2>&1 && command -v git >/dev/null 2>&1; then
  info "Building Plannotator portal static files..."

  build_ok=1
  if [[ -d "$BUILD_DIR/.git" ]]; then
    if ! git -C "$BUILD_DIR" pull --ff-only; then
      warn "Portal build failed — Caddy will 404 on / until rebuilt"
      build_ok=0
    fi
  else
    rm -rf "$BUILD_DIR"
    if ! git clone --depth 1 https://github.com/backnotprop/plannotator.git "$BUILD_DIR"; then
      warn "Portal build failed — Caddy will 404 on / until rebuilt"
      build_ok=0
    fi
  fi

  if [[ "$build_ok" -eq 1 ]]; then
    if (cd "$BUILD_DIR" && bun install && bun run build:portal); then
      if [[ -d "$BUILD_DIR/dist/portal" ]]; then
        rm -rf "$PORTAL_DIR"
        mkdir -p "$PORTAL_DIR"
        cp -R "$BUILD_DIR/dist/portal/." "$PORTAL_DIR/"
        ok "Plannotator portal built at ${PORTAL_DIR}"
      else
        warn "Portal build output missing — Caddy will 404 on / until rebuilt"
      fi
    else
      warn "Portal build failed — Caddy will 404 on / until rebuilt"
    fi
  fi
else
  info "bun or git not found — skipping Plannotator portal build"
fi

info "Plannotator setup complete!\n\nBinary: ${BIN_PATH}\nData:   ${DATA_DIR}\nPortal: ${PORTAL_DIR}"
