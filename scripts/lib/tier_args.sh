#!/usr/bin/env bash

# tier_args.sh — Shared model override argument forwarding for chezmoi scripts.
#
# Usage:
#   source "${LIB_DIR}/tier_args.sh"
#   build_tier_extra_args
#   # TIER_EXTRA_ARGS is now populated
#
# Reads from environment:
#   DOTFILES_LOCAL_FALLBACK_PRESET
#   DOTFILES_CATEGORY_MODELS (deprecated: DOTFILES_LOCAL_FALLBACK_PLACEHOLDERS)
#   DOTFILES_ROLE_MODELS (deprecated: DOTFILES_LOCAL_FALLBACK_ROLES)

build_tier_extra_args() {
  TIER_EXTRA_ARGS=()
  if [[ -n "${DOTFILES_MIN_REASONING_EMBEDDING:-}" ]]; then
    TIER_EXTRA_ARGS+=("--min-reasoning-embedding" "$DOTFILES_MIN_REASONING_EMBEDDING")
  fi
  if [[ -n "${DOTFILES_LOCAL_FALLBACK_PRESET:-}" ]]; then
    TIER_EXTRA_ARGS+=("--local-fallback-preset" "$DOTFILES_LOCAL_FALLBACK_PRESET")
  fi
  local category_models="${DOTFILES_CATEGORY_MODELS:-${DOTFILES_LOCAL_FALLBACK_PLACEHOLDERS:-}}"
  if [[ -n "${category_models}" ]]; then
    if [[ -z "${DOTFILES_CATEGORY_MODELS:-}" ]]; then
      warn "Deprecated DOTFILES_LOCAL_FALLBACK_PLACEHOLDERS; use DOTFILES_CATEGORY_MODELS instead"
    fi
    local IFS=','
    local -a pholder_overrides
    read -ra pholder_overrides <<<"$category_models"
    for override in "${pholder_overrides[@]}"; do
      [[ -n "$override" ]] && TIER_EXTRA_ARGS+=("--category-model" "$override")
    done
  fi
  local role_models="${DOTFILES_ROLE_MODELS:-${DOTFILES_LOCAL_FALLBACK_ROLES:-}}"
  if [[ -n "${role_models}" ]]; then
    if [[ -z "${DOTFILES_ROLE_MODELS:-}" ]]; then
      warn "Deprecated DOTFILES_LOCAL_FALLBACK_ROLES; use DOTFILES_ROLE_MODELS instead"
    fi
    local IFS=','
    local -a role_overrides
    read -ra role_overrides <<<"$role_models"
    for override in "${role_overrides[@]}"; do
      [[ -n "$override" ]] && TIER_EXTRA_ARGS+=("--role-model" "$override")
    done
  fi
}
