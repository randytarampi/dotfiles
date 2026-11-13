#!/usr/bin/env bash
# Sourced by the package lane before any bundle when local_ai_headless is on.
# Read-only: do not stop processes, uninstall apps, or delete user state.

ollama_brew_package_preflight() {
  local desktop_dev="${1:-false}"
  local cask

  if [[ "$(uname -s)" != "Darwin" ]]; then
    return 0
  fi

  if [[ "$desktop_dev" == "true" ]]; then
    printf '%s\n' "Refusing headless Ollama formula while desktop_dev would install Ollama.app" >&2
    return 1
  fi

  local applications_dir="${OLLAMA_BREW_PREFLIGHT_APPLICATIONS_DIR:-/Applications}"
  if [[ -d "$applications_dir/Ollama.app" || -d "$HOME/Applications/Ollama.app" ]]; then
    printf '%s\n' "Refusing headless Ollama formula: Ollama.app exists; migrate explicitly first" >&2
    return 1
  fi

  for cask in ollama-app ollama-binary; do
    if [[ -n "$(brew list --cask --versions "$cask" 2>/dev/null || true)" ]]; then
      printf '%s\n' "Refusing headless Ollama formula: Homebrew cask $cask is installed" >&2
      return 1
    fi
  done
}
