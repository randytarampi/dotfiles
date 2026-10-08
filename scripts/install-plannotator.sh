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
export COMMON_STRICT=1
parse_common_args "$@"
set -- ${COMMON_ARGS_REMAINING[@]+"${COMMON_ARGS_REMAINING[@]}"}
if [[ "$COMMON_NO_BACKUP" == "1" ]]; then
  printf '%s\n' "Error: --no-backup is not supported by this script" >&2
  exit 2
fi
# shellcheck disable=SC1091
source "$LIB_DIR/env.sh"

# Floating tracking follows the repo's @latest dependency policy
# (docs/CONVENTIONS.md, OpenCode plugins subsection): resolve the newest
# release at run time. When resolution fails (offline etc.), fall back to the
# installed marker version so a healthy install is never churned.
PLANNOTATOR_VERSION="$(gh api repos/backnotprop/plannotator/releases/latest --jq .tag_name 2>/dev/null || true)"
PLANNOTATOR_VERSION="${PLANNOTATOR_VERSION#v}"
PLANNOTATOR_TAG="v${PLANNOTATOR_VERSION}"

load_env || warn "\$HOME/.env not found, skipping env load"

if [[ "${DOTFILES_RUN_PLANNOTATOR_PASTE_SETUP:-${DOTFILES_RUN_CADDY_SETUP:-0}}" != "1" ]]; then
  info "DOTFILES_RUN_PLANNOTATOR_PASTE_SETUP='${DOTFILES_RUN_PLANNOTATOR_PASTE_SETUP:-${DOTFILES_RUN_CADDY_SETUP:-0}}' — skipping Plannotator paste install"
  exit 0
fi

if [[ "$COMMON_DRY_RUN" == "1" ]]; then
  info "[DRY RUN] Would install or update Plannotator paste and portal files without changing binaries, data, or services"
  exit 0
fi

# Install binary to brew prefix if available, else ~/.local/bin
# Detect the platform FIRST: on Windows the binary target gains ".exe", and the
# version marker must be derived from that final BIN_PATH so reads and writes
# always agree.
case "$(uname -s)/$(uname -m)" in
Darwin/arm64) ARCH="darwin-arm64" ;;
Darwin/x86_64) ARCH="darwin-x64" ;;
Linux/arm64 | Linux/aarch64) ARCH="linux-arm64" ;;
Linux/x86_64) ARCH="linux-x64" ;;
MINGW*/arm64 | MSYS*/arm64 | CYGWIN*/arm64) ARCH="win32-arm64" ;;
MINGW*/x86_64 | MSYS*/x86_64 | CYGWIN*/x86_64) ARCH="win32-x64" ;;
*) die "Unsupported platform: $(uname -s)/$(uname -m)" ;;
esac
WIN_ARCH=0
if [[ "$ARCH" == win32-* ]]; then
  WIN_ARCH=1
fi

if command -v brew >/dev/null 2>&1; then
  BREW_PREFIX="$(brew --prefix)"
  BIN_PATH="$BREW_PREFIX/bin/plannotator-paste"
else
  BIN_PATH="${HOME}/.local/bin/plannotator-paste"
  mkdir -p "$(dirname "$BIN_PATH")"
fi
if [[ "$WIN_ARCH" == 1 ]]; then
  BIN_PATH="${BIN_PATH}.exe"
fi
DATA_DIR="${PASTE_DATA_DIR:-$HOME/.plannotator/pastes}"
PORTAL_DIR="$HOME/.plannotator/portal"
BUILD_DIR="/tmp/plannotator-build"
PINNED_VERSION="${PLANNOTATOR_VERSION}"

PRESENT_BIN="$(command -v plannotator-paste 2>/dev/null || true)"
# Canonical install target is always BIN_PATH; a PATH-resolved binary outside
# it is not ours — only BIN_PATH's marker can be trusted for version tracking.
VERSION_MARKER="${BIN_PATH}.version"
INSTALLED_VERSION=""
if [[ -f "$VERSION_MARKER" ]]; then
  INSTALLED_VERSION="$(<"$VERSION_MARKER")"
fi
if [[ -z "$PINNED_VERSION" && -n "$INSTALLED_VERSION" ]]; then
  # Release resolution failed (offline etc.): target the version that is
  # already installed so a healthy install is neither churned nor wiped.
  warn "Could not resolve the latest Plannotator release — keeping installed ${INSTALLED_VERSION}"
  PLANNED_VERSION="$INSTALLED_VERSION"
  PLANNED_TAG="v${PLANNED_VERSION}"
  SKIP_INSTALL=1
else
  PLANNED_VERSION="$PINNED_VERSION"
  PLANNED_TAG="$PLANNOTATOR_TAG"
  SKIP_INSTALL=0
fi
if [[ "$SKIP_INSTALL" == "1" || (-x "$BIN_PATH" && "$INSTALLED_VERSION" == "$PLANNED_VERSION") ]]; then
  ok "Plannotator paste ${PLANNED_VERSION} already installed at ${BIN_PATH}"
else
  if [[ -x "$BIN_PATH" ]]; then
    info "Replacing Plannotator paste ${INSTALLED_VERSION:-unknown} with ${PLANNED_VERSION}"
  elif [[ -n "$PRESENT_BIN" && "$PRESENT_BIN" != "$BIN_PATH" ]]; then
    info "Found plannotator-paste outside the canonical path (${PRESENT_BIN}); installing ${PLANNED_VERSION} to ${BIN_PATH}"
  fi
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
  DOWNLOAD_URL="https://github.com/backnotprop/plannotator/releases/download/${PLANNED_TAG}/plannotator-paste-${ARCH}"
  if [[ "$WIN_ARCH" == 1 ]]; then
    DOWNLOAD_URL="${DOWNLOAD_URL}.exe"
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
  printf '%s\n' "$PLANNED_VERSION" >"${BIN_PATH}.version"
  ok "Plannotator paste installed to ${BIN_PATH}"
fi

mkdir -p "$DATA_DIR"
mkdir -p "$(dirname "$PORTAL_DIR")"

if command -v bun >/dev/null 2>&1 && command -v git >/dev/null 2>&1; then
  info "Building Plannotator portal static files..."

  build_ok=1
  if [[ -d "$BUILD_DIR/.git" ]]; then
    if ! git -C "$BUILD_DIR" fetch --tags --force origin "$PLANNED_TAG" || ! git -C "$BUILD_DIR" checkout --force "$PLANNED_TAG"; then
      warn "Portal build failed — Caddy will 404 on / until rebuilt"
      build_ok=0
    fi
  else
    rm -rf "$BUILD_DIR"
    if ! git clone --depth 1 --branch "$PLANNED_TAG" https://github.com/backnotprop/plannotator.git "$BUILD_DIR"; then
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
