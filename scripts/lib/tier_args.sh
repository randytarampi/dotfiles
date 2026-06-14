#!/usr/bin/env bash

# tier_args.sh — Shared local fallback argument forwarding for chezmoi scripts.
#
# Usage:
#   source "${LIB_DIR}/tier_args.sh"
#   build_tier_extra_args
#   # TIER_EXTRA_ARGS is now populated
#
# Reads from environment:
#   DOTFILES_LOCAL_FALLBACK_PRESET
#   DOTFILES_LOCAL_FALLBACK_PLACEHOLDERS
#   DOTFILES_LOCAL_FALLBACK_ROLES

build_tier_extra_args() {
  TIER_EXTRA_ARGS=()
  if [[ -n "${DOTFILES_LOCAL_FALLBACK_PRESET:-}" ]]; then
    TIER_EXTRA_ARGS+=("--local-fallback-preset" "$DOTFILES_LOCAL_FALLBACK_PRESET")
  fi
  if [[ -n "${DOTFILES_LOCAL_FALLBACK_PLACEHOLDERS:-}" ]]; then
    local IFS=','
    local -a pholder_overrides
    read -ra pholder_overrides <<<"$DOTFILES_LOCAL_FALLBACK_PLACEHOLDERS"
    for override in "${pholder_overrides[@]}"; do
      [[ -n "$override" ]] && TIER_EXTRA_ARGS+=("--local-fallback-placeholder" "$override")
    done
  fi
  if [[ -n "${DOTFILES_LOCAL_FALLBACK_ROLES:-}" ]]; then
    local IFS=','
    local -a role_overrides
    read -ra role_overrides <<<"$DOTFILES_LOCAL_FALLBACK_ROLES"
    for override in "${role_overrides[@]}"; do
      [[ -n "$override" ]] && TIER_EXTRA_ARGS+=("--local-fallback-role" "$override")
    done
  fi
}
