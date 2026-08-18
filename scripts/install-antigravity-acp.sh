#!/usr/bin/env bash
set -euo pipefail

_SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"

# ─── install-antigravity-acp.sh ──────────────────────────────────────────────
#
# Installs the antigravity-acp bridge (shubzkothekar/antigravity-acp) so ACP-
# compatible editors (OpenCode, etc.) can drive the Google Antigravity CLI
# (`agy`) over stdio. Run once per machine.
#
# Does NOT write any configuration files — `configure-acp-agents.py` handles
# ACP registration during OpenCode config generation.
#
# What this installs:
#   1. Prebuilt platform binary `agy-acp` symlinked into ~/.local/bin/
#
# Prerequisites:
#   - `bun` (build-time only; not needed at runtime)
#   - `agy` on PATH (auto-detected by the bridge; installed via Brewfile.desktop)
#
# ⚠️  ToS warning: Google's Antigravity ToS prohibit using third-party software
#     to access the Service. Routing Antigravity OAuth through this bridge may
#     lead to account suspension. Use Vertex AI / AI Studio API keys instead
#     if this risk is unacceptable.
# ───────────────────────────────────────────────────────────────────────────────

source "$SCRIPT_DIR/lib/common.sh"
source "$SCRIPT_DIR/lib/common_args.sh"
export COMMON_USAGE="$0"
export COMMON_HELP_TEXT="Install the Antigravity ACP bridge."
parse_common_args "$@"
set -- ${COMMON_ARGS_REMAINING[@]+"${COMMON_ARGS_REMAINING[@]}"}

# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------
ANTIGRAVITY_ACP_SETUP="${DOTFILES_RUN_ANTIGRAVITY_ACP_SETUP:-0}"
if [[ "$ANTIGRAVITY_ACP_SETUP" != "1" ]]; then
  info "DOTFILES_RUN_ANTIGRAVITY_ACP_SETUP='${ANTIGRAVITY_ACP_SETUP}' — skipping antigravity-acp install"
  exit 0
fi

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------
info "Checking prerequisites..."

if ! command -v bun &>/dev/null; then
  warn "'bun' not found — skipping antigravity-acp install (build-time dependency)"
  warn "Install Bun first: curl -fsSL https://bun.sh | bash"
  exit 0
fi

if ! command -v agy &>/dev/null; then
  warn "'agy' not found on PATH — the bridge will auto-download it, but this is unexpected"
  warn "Install agy first: brew install --cask antigravity-cli"
fi

# Detect platform → build target suffix
ARCH="$(uname -m)"
case "$ARCH" in
arm64)
  BUILD_TARGET="build:mac-arm64"
  BINARY_NAME="agy-acp-darwin-arm64"
  ;;
*)
  warn "Unsupported architecture: ${ARCH} — skipping antigravity-acp install"
  warn "Only macOS arm64 is currently supported"
  exit 0
  ;;
esac

# ---------------------------------------------------------------------------
# 1. Clone / update repo
# ---------------------------------------------------------------------------
INSTALL_DIR="${HOME}/.local/share/antigravity-acp"
if [[ -d "$INSTALL_DIR/.git" ]]; then
  info "Updating antigravity-acp at ${INSTALL_DIR}..."
  if git -C "$INSTALL_DIR" pull --ff-only; then
    ok "antigravity-acp repository updated"
  else
    warn "Failed to update antigravity-acp repository — continuing with existing checkout"
  fi
else
  info "Cloning antigravity-acp to ${INSTALL_DIR}..."
  mkdir -p "$(dirname "$INSTALL_DIR")"
  if git clone https://github.com/shubzkothekar/antigravity-acp.git "$INSTALL_DIR"; then
    ok "antigravity-acp repository cloned"
  else
    die "Failed to clone antigravity-acp repository"
  fi
fi

# ---------------------------------------------------------------------------
# 2. Install dependencies + build prebuilt binary
# ---------------------------------------------------------------------------
info "Installing dependencies via bun..."
if ! (cd "$INSTALL_DIR" && bun install); then
  die "bun install failed in antigravity-acp repository"
fi

info "Building prebuilt binary (${BUILD_TARGET})..."
if ! (cd "$INSTALL_DIR" && bun run "$BUILD_TARGET"); then
  die "Failed to build antigravity-acp prebuilt binary"
fi

BINARY_PATH="${INSTALL_DIR}/dist/${BINARY_NAME}"
if [[ ! -x "$BINARY_PATH" ]]; then
  die "Expected built binary not found or not executable: ${BINARY_PATH}"
fi

# ---------------------------------------------------------------------------
# 3. Symlink into ~/.local/bin
# ---------------------------------------------------------------------------
LOCAL_BIN="${HOME}/.local/bin"
mkdir -p "$LOCAL_BIN"

LINK_PATH="${LOCAL_BIN}/agy-acp"
if [[ -L "$LINK_PATH" ]]; then
  rm "$LINK_PATH"
fi
ln -s "$BINARY_PATH" "$LINK_PATH"
ok "Symlinked ${BINARY_PATH} → ${LINK_PATH}"

hash -r 2>/dev/null || true

# ---------------------------------------------------------------------------
# 4. Verify installation
# ---------------------------------------------------------------------------
info "Verifying antigravity-acp installation..."
if command -v agy-acp &>/dev/null; then
  ok "antigravity-acp verified at $(command -v agy-acp)"
else
  warn "agy-acp not found on PATH after install — ensure ${LOCAL_BIN} is in your PATH"
fi

# ---------------------------------------------------------------------------
# Done!
# ---------------------------------------------------------------------------
info "antigravity-acp installed!

Next steps:
  1. Authenticate agy:  agy auth login  (Google Sign-In via system keyring)
  2. Deploy ACP reg:   make deploy  (configure-acp-agents.py will pick up agy-acp)
  3. Test in OpenCode: /agent @antigravity hello

⚠️  ToS warning: Google's Antigravity ToS prohibit using third-party software
    to access the Service. Routing Antigravity OAuth through this bridge may
    lead to account suspension. Use Vertex AI / AI Studio API keys instead
    if this risk is unacceptable.

Install script complete!"
