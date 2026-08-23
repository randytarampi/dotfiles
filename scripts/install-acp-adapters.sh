#!/usr/bin/env bash
set -euo pipefail

# Install ACP (Agent Client Protocol) adapters for OpenCode:
#   - The Google Antigravity bridge (`agy-acp`)
#   - GitHub Copilot CLI
#   - Claude Code ACP adapter
#   - Codex ACP adapter

_SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"

source "$SCRIPT_DIR/lib/common.sh"
source "$SCRIPT_DIR/lib/common_args.sh"
export COMMON_USAGE="$0"
export COMMON_HELP_TEXT="Install ACP (Agent Client Protocol) adapters for OpenCode."
export COMMON_STRICT=1
parse_common_args "$@"
set -- ${COMMON_ARGS_REMAINING[@]+"${COMMON_ARGS_REMAINING[@]}"}

install_antigravity_acp() {
  local install_dir="${HOME}/.local/share/antigravity-acp"
  local local_bin="${HOME}/.local/bin"
  local link_path="${local_bin}/agy-acp"
  local arch build_target binary_name binary_path

  if [[ "${DOTFILES_RUN_ANTIGRAVITY_ACP_SETUP:-0}" != "1" ]]; then
    info "DOTFILES_RUN_ANTIGRAVITY_ACP_SETUP='${DOTFILES_RUN_ANTIGRAVITY_ACP_SETUP:-0}' — skipping antigravity-acp install"
    return 0
  fi

  info "Checking Antigravity ACP prerequisites..."
  if ! command -v bun >/dev/null 2>&1; then
    warn "'bun' not found — skipping antigravity-acp install (build-time dependency)"
    warn "Install Bun first: curl -fsSL https://bun.sh | bash"
    return 0
  fi
  if ! command -v agy >/dev/null 2>&1; then
    warn "'agy' not found on PATH — the bridge will auto-download it, but this is unexpected"
    warn "Install agy first: brew install --cask antigravity-cli"
  fi

  arch="$(uname -m)"
  case "$arch" in
  arm64)
    build_target="build:mac-arm64"
    binary_name="agy-acp-darwin-arm64"
    ;;
  *)
    warn "Unsupported architecture: ${arch} — skipping antigravity-acp install"
    warn "Only macOS arm64 is currently supported"
    return 0
    ;;
  esac

  if [[ "$COMMON_DRY_RUN" == "1" ]]; then
    info "[DRY RUN] Would clone/update ${install_dir}, build ${binary_name}, and symlink ${link_path}"
    return 0
  fi

  if [[ -d "$install_dir/.git" ]]; then
    info "Updating antigravity-acp at ${install_dir}..."
    if git -C "$install_dir" pull --ff-only; then
      ok "antigravity-acp repository updated"
    else
      warn "Failed to update antigravity-acp repository — continuing with existing checkout"
    fi
  else
    info "Cloning antigravity-acp to ${install_dir}..."
    mkdir -p "$(dirname "$install_dir")"
    if git clone https://github.com/shubzkothekar/antigravity-acp.git "$install_dir"; then
      ok "antigravity-acp repository cloned"
    else
      die "Failed to clone antigravity-acp repository"
    fi
  fi

  info "Installing dependencies via bun..."
  if ! (cd "$install_dir" && bun install); then
    die "bun install failed in antigravity-acp repository"
  fi
  info "Building prebuilt binary (${build_target})..."
  if ! (cd "$install_dir" && bun run "$build_target"); then
    die "Failed to build antigravity-acp prebuilt binary"
  fi

  binary_path="${install_dir}/dist/${binary_name}"
  if [[ ! -x "$binary_path" ]]; then
    die "Expected built binary not found or not executable: ${binary_path}"
  fi
  mkdir -p "$local_bin"
  rm -f "$link_path"
  ln -s "$binary_path" "$link_path"
  ok "Symlinked ${binary_path} → ${link_path}"
  hash -r 2>/dev/null || true

  info "Verifying antigravity-acp installation..."
  if command -v agy-acp >/dev/null 2>&1; then
    ok "antigravity-acp verified at $(command -v agy-acp)"
  else
    warn "agy-acp not found on PATH after install — ensure ${local_bin} is in your PATH"
  fi
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
}

install_opencode_adapters() {
  if [[ "${DOTFILES_RUN_OPENCODE_TOOLS_SETUP:-0}" != "1" ]]; then
    info "DOTFILES_RUN_OPENCODE_TOOLS_SETUP='${DOTFILES_RUN_OPENCODE_TOOLS_SETUP:-0}' — skipping Copilot, Claude, and Codex ACP adapters"
    return 0
  fi

  if command -v copilot >/dev/null 2>&1; then
    ok "GitHub Copilot CLI already installed"
  elif [[ "$COMMON_DRY_RUN" == "1" ]]; then
    info "[DRY RUN] Would install GitHub Copilot CLI with brew install copilot-cli"
  else
    info "Installing GitHub Copilot CLI..."
    if brew install copilot-cli 2>/dev/null; then
      ok "GitHub Copilot CLI installed"
    else
      warn "Failed to install GitHub Copilot CLI (public preview — may require manual install)"
    fi
  fi

  install_npm_adapter "@agentclientprotocol/claude-agent-acp"
  install_npm_adapter "@agentclientprotocol/codex-acp"
  install_npm_adapter "pi-acp"
}

install_npm_adapter() {
  local package="$1"
  if npm list -g "$package" >/dev/null 2>&1; then
    ok "$package already installed"
  elif [[ "$COMMON_DRY_RUN" == "1" ]]; then
    info "[DRY RUN] Would install ${package}@latest globally"
  else
    info "Installing ${package}..."
    if npm install -g "${package}@latest" 2>/dev/null; then
      ok "${package} installed"
    else
      warn "Failed to install ${package}"
    fi
  fi
}

install_cursor_agent() {
  if [[ "${DOTFILES_RUN_OPENCODE_TOOLS_SETUP:-0}" != "1" ]]; then
    info "DOTFILES_RUN_OPENCODE_TOOLS_SETUP='${DOTFILES_RUN_OPENCODE_TOOLS_SETUP:-0}' — skipping Cursor agent CLI"
    return 0
  fi

  if command -v cursor-agent >/dev/null 2>&1; then
    ok "Cursor agent CLI already installed"
    info "Next step: cursor-agent login (browser auth via Cursor account)"
    return 0
  fi

  if ! command -v cursor >/dev/null 2>&1; then
    warn "'cursor' not found — skipping cursor-agent install (Cursor IDE required)"
    return 0
  fi

  if [[ "$COMMON_DRY_RUN" == "1" ]]; then
    info "[DRY RUN] Would trigger cursor-agent auto-install via 'cursor agent --version'"
    return 0
  fi

  info "Triggering cursor-agent auto-install (first 'cursor agent' invocation)..."
  # Cursor's first-run mechanism installs cursor-agent and agent wrappers to ~/.local/bin/
  cursor agent --version 2>/dev/null || true
  hash -r 2>/dev/null || true

  if command -v cursor-agent >/dev/null 2>&1; then
    ok "Cursor agent CLI installed at $(command -v cursor-agent)"
  else
    warn "cursor-agent not found on PATH after auto-install — ensure ~/.local/bin is in your PATH"
    warn "Try running 'cursor agent' manually to trigger the install"
  fi
  info "Next step: cursor-agent login (browser auth via Cursor account)"
}

install_antigravity_acp
install_cursor_agent
install_opencode_adapters
