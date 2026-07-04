#!/usr/bin/env bash

# tier_detect.sh — Shared tier auto-detection for chezmoi scripts.
#
# Usage:
#   source "${LIB_DIR}/tier_detect.sh"
#   detect_tier DOTFILES_OPENCODE_TIER
#   # Sets: TIER, _has_openai, _has_anthropic, _has_ollama_cloud, _has_ollama
#
# The override env var name controls which DOTFILES_*_TIER variable
# is checked for manual tier selection.

detect_tier() {
  local override_var="${1:-DOTFILES_OPENCODE_TIER}"
  local override_value="${!override_var:-}"

  TIER="pro"
  _has_openai=false
  _has_anthropic=false
  _has_ollama_cloud=false
  _has_ollama=false

  # Check env vars first (explicit API keys take precedence)
  if [[ -n "${OPENAI_API_KEY:-}" ]]; then
    _has_openai=true
  fi
  if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    _has_anthropic=true
  fi
  if [[ -n "${OLLAMA_API_KEY:-}" ]]; then
    _has_ollama_cloud=true
  fi

  # CLI auth fallback: a CLI on PATH can signal provider access via its own
  # OAuth login — but only when the user hasn't explicitly declared the key
  # in ~/.env. A set-but-empty assignment (`KEY=''`) is an explicit opt-out
  # and must override the CLI signal. `-z "${VAR+set}"` is true only when VAR
  # is unset (never declared), so the fallback fires only for absent keys.
  if command -v codex >/dev/null 2>&1 && [[ -z "${OPENAI_API_KEY+set}" ]]; then
    _has_openai=true
  fi
  if command -v claude >/dev/null 2>&1 && [[ -z "${ANTHROPIC_API_KEY+set}" ]]; then
    _has_anthropic=true
  fi

  # Check for local Ollama. The opt-in toggle controls whether discovered local
  # models are included, but it should not pretend Ollama exists when the binary
  # is unavailable.
  if command -v ollama >/dev/null 2>&1; then
    _has_ollama=true
  fi

  # Tier auto-detection — covers all 11 tiers (6 cloud + 5 local)
  # local-pro, local-mini, local-nano, local-solo are manual-only (set via override var)
  if [[ -n "$override_value" ]]; then
    TIER="$override_value"
    info "Tier override from ${override_var}: $TIER"
  elif [[ "$_has_ollama_cloud" == true ]] && [[ "$_has_openai" == true ]] && [[ "$_has_anthropic" == true ]]; then
    TIER="pro-plus-anthropic"
  elif [[ "$_has_ollama_cloud" == true ]] && [[ "$_has_openai" == true ]]; then
    TIER="pro-plus"
  elif [[ "$_has_ollama_cloud" == true ]] && [[ "$_has_anthropic" == true ]]; then
    TIER="pro-plus-anthropic"
  elif [[ "$_has_ollama_cloud" == true ]]; then
    TIER="pro"
  elif [[ "$_has_openai" == true ]] && [[ "$_has_anthropic" == true ]]; then
    TIER="plus-anthropic"
  elif [[ "$_has_openai" == true ]]; then
    TIER="plus"
  elif [[ "$_has_anthropic" == true ]]; then
    TIER="anthropic"
  elif [[ "$_has_ollama" == true ]]; then
    TIER="local"
  fi

  info "Detected tier: $TIER (ollama_cloud: $_has_ollama_cloud, openai: $_has_openai, anthropic: $_has_anthropic, ollama: $_has_ollama)"
}
