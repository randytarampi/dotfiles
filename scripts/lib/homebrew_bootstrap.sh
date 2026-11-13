#!/usr/bin/env bash
# Resolve Homebrew for the opt-in package lane without touching host state
# when brew is already installed. Intended to be sourced by chezmoi.

_dotfiles_brew_binary() {
  local candidate
  if command -v brew >/dev/null 2>&1; then
    candidate="$(command -v brew)"
    [[ -x "$candidate" ]] && {
      printf '%s\n' "$candidate"
      return 0
    }
  fi

  local -a candidates=()
  if [[ -n "${HOMEBREW_PREFIX:-}" ]]; then
    candidates+=("${HOMEBREW_PREFIX}/bin/brew")
  fi
  candidates+=(
    /opt/homebrew/bin/brew
    /usr/local/bin/brew
    /home/linuxbrew/.linuxbrew/bin/brew
    "$HOME/.linuxbrew/bin/brew"
  )
  for candidate in "${candidates[@]}"; do
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

dotfiles_homebrew_ready() {
  local brew_binary install_script brew_shellenv
  if ! brew_binary="$(_dotfiles_brew_binary)"; then
    # Test download success *before* passing the complete script to bash.
    if ! install_script="$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"; then
      printf 'Cannot download the official Homebrew installer\n' >&2
      return 1
    fi
    if [[ -z "$install_script" ]]; then
      printf 'Empty Homebrew installer; refusing to execute\n' >&2
      return 1
    fi
    if ! /bin/bash -c "$install_script"; then
      printf 'Homebrew installation failed\n' >&2
      return 1
    fi
    if ! brew_binary="$(_dotfiles_brew_binary)"; then
      printf 'Homebrew installer completed but brew is not discoverable\n' >&2
      return 1
    fi
  fi

  # shellenv is supplied by the located local binary, never directly by curl.
  # Capture its exit status: eval "" otherwise masks a failing subprocess.
  if ! brew_shellenv="$("$brew_binary" shellenv)"; then
    printf 'Homebrew shellenv failed for %s\n' "$brew_binary" >&2
    return 1
  fi
  eval "$brew_shellenv"
  if ! command -v brew >/dev/null 2>&1; then
    printf 'Homebrew shellenv did not add brew to PATH\n' >&2
    return 1
  fi
}
