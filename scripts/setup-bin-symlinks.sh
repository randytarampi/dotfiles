#!/usr/bin/env bash
set -euo pipefail

# setup-bin-symlinks.sh — Create/update _dot--* symlinks in ~/.dotfiles/bin/
# Usage: setup-bin-symlinks.sh [SOURCE_DIR]
#   SOURCE_DIR defaults to the directory containing this script's parent (scripts/)

SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
LIB_DIR="$(cd "$(dirname "$SELF")/lib" && pwd)"
source "${LIB_DIR}/common.sh"

SOURCE_SCRIPTS="${1:-$(cd "$(dirname "$SELF")" && pwd)}"
DOTFILES_BIN="$HOME/.dotfiles/bin"
DOTFILES_SCRIPTS="$HOME/.dotfiles/scripts"

info "Setting up bin symlinks..."

# Ensure scripts symlink exists
if [[ ! -L "$DOTFILES_SCRIPTS" && ! -d "$DOTFILES_SCRIPTS" ]]; then
  ln -s "$SOURCE_SCRIPTS" "$DOTFILES_SCRIPTS"
  info "Created scripts symlink: $DOTFILES_SCRIPTS → $SOURCE_SCRIPTS"
fi

# Create bin directory
mkdir -p "$DOTFILES_BIN"

created=0
skipped=0
removed=0

# Create _dot--* command symlinks for all scripts
for script in "$SOURCE_SCRIPTS"/*.py "$SOURCE_SCRIPTS"/*.sh; do
  [[ -f "$script" ]] || continue
  base="$(basename "$script")"
  base="${base%.py}"
  base="${base%.sh}"
  link_name="_dot--${base}"
  target_link="$DOTFILES_BIN/$link_name"
  if [[ ! -L "$target_link" ]]; then
    ln -s "$script" "$target_link"
    info "Created symlink: $target_link → $script"
    created=$((created + 1))
  else
    # Update symlink target if script path changed
    current_target="$(readlink "$target_link")"
    if [[ "$current_target" != "$script" ]]; then
      ln -sf "$script" "$target_link"
      info "Updated symlink: $target_link → $script"
      created=$((created + 1))
    else
      skipped=$((skipped + 1))
    fi
  fi
done

# Remove stale symlinks (scripts that no longer exist)
for link in "$DOTFILES_BIN"/_dot--*; do
  [[ -L "$link" ]] || continue
  target="$(readlink "$link")"
  if [[ ! -f "$target" ]]; then
    rm "$link"
    info "Removed stale symlink: $link → $target"
    removed=$((removed + 1))
  fi
done

ok "Bin symlinks: ${created} created, ${skipped} existing, ${removed} removed."
