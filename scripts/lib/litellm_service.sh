#!/usr/bin/env bash

litellm_service_domain() { printf 'gui/%s\n' "${UID:-$(id -u)}"; }
litellm_service_plist() { printf '%s\n' "$HOME/Library/LaunchAgents/com.litellm.proxy.plist"; }

litellm_service_env_sync() {
  local service_env="${1:-$HOME/.local/share/litellm/service.env}" tmp
  local LITELLM_MASTER_KEY="" LITELLM_PORT=""
  local OMLX_API_KEY="" MERIDIAN_API_KEY="" OPENAI_API_KEY="" ANTHROPIC_API_KEY="" GEMINI_API_KEY="" OPENROUTER_API_KEY=""
  mkdir -p "$(dirname "$service_env")"
  local env_LITELLM_MASTER_KEY="" env_LITELLM_PORT=""
  local env_OMLX_API_KEY="" env_MERIDIAN_API_KEY="" env_OPENAI_API_KEY="" env_ANTHROPIC_API_KEY="" env_GEMINI_API_KEY="" env_OPENROUTER_API_KEY=""
  if [[ -f "$HOME/.env" ]]; then
    # shellcheck disable=SC1091
    source "$HOME/.env"
  fi
  env_LITELLM_MASTER_KEY="$LITELLM_MASTER_KEY"
  env_LITELLM_PORT="$LITELLM_PORT"
  env_OMLX_API_KEY="$OMLX_API_KEY"
  env_MERIDIAN_API_KEY="$MERIDIAN_API_KEY"
  env_OPENAI_API_KEY="$OPENAI_API_KEY"
  env_ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY"
  env_GEMINI_API_KEY="$GEMINI_API_KEY"
  env_OPENROUTER_API_KEY="$OPENROUTER_API_KEY"
  if [[ -f "$service_env" ]]; then
    # shellcheck disable=SC1090
    source "$service_env"
  fi
  [[ -n "$env_LITELLM_MASTER_KEY" ]] && LITELLM_MASTER_KEY="$env_LITELLM_MASTER_KEY"
  [[ -n "$env_LITELLM_PORT" ]] && LITELLM_PORT="$env_LITELLM_PORT"
  [[ -n "$env_OMLX_API_KEY" ]] && OMLX_API_KEY="$env_OMLX_API_KEY"
  [[ -n "$env_MERIDIAN_API_KEY" ]] && MERIDIAN_API_KEY="$env_MERIDIAN_API_KEY"
  [[ -n "$env_OPENAI_API_KEY" ]] && OPENAI_API_KEY="$env_OPENAI_API_KEY"
  [[ -n "$env_ANTHROPIC_API_KEY" ]] && ANTHROPIC_API_KEY="$env_ANTHROPIC_API_KEY"
  [[ -n "$env_GEMINI_API_KEY" ]] && GEMINI_API_KEY="$env_GEMINI_API_KEY"
  [[ -n "$env_OPENROUTER_API_KEY" ]] && OPENROUTER_API_KEY="$env_OPENROUTER_API_KEY"
  unset OMLX_API_KEY MERIDIAN_API_KEY OPENAI_API_KEY ANTHROPIC_API_KEY GEMINI_API_KEY OPENROUTER_API_KEY LITELLM_PORT
  [[ -n "$env_LITELLM_PORT" ]] && LITELLM_PORT="$env_LITELLM_PORT"
  local config_path
  config_path="$(dirname "$service_env")/config.yaml"
  if [[ -f "$config_path" ]]; then
    for provider_key in OMLX_API_KEY MERIDIAN_API_KEY OPENAI_API_KEY ANTHROPIC_API_KEY GEMINI_API_KEY OPENROUTER_API_KEY; do
      grep -Fq "os.environ/${provider_key}" "$config_path" || unset "$provider_key"
    done
  fi
  LITELLM_PORT="${LITELLM_PORT:-4000}"
  if [[ -z "$LITELLM_MASTER_KEY" ]]; then
    command -v openssl >/dev/null 2>&1 || return 1
    local payload
    if ! payload="$(openssl rand -hex 32)" || [[ -z "$payload" ]]; then
      return 1
    fi
    LITELLM_MASTER_KEY="sk-${payload}"
  fi
  if [[ "$LITELLM_MASTER_KEY" != sk-* || ${#LITELLM_MASTER_KEY} -lt 16 ]]; then
    printf 'Invalid LiteLLM master key (masked): <set:%s>\n' "${#LITELLM_MASTER_KEY}" >&2
    return 1
  fi
  tmp="${service_env}.tmp.$$"
  umask 077
  {
    printf 'LITELLM_MASTER_KEY=%q\n' "$LITELLM_MASTER_KEY"
    printf 'LITELLM_PORT=%q\n' "$LITELLM_PORT"
    [[ -n "${OMLX_API_KEY:-}" ]] && printf 'OMLX_API_KEY=%q\n' "$OMLX_API_KEY"
    [[ -n "${MERIDIAN_API_KEY:-}" ]] && printf 'MERIDIAN_API_KEY=%q\n' "$MERIDIAN_API_KEY"
    [[ -n "${OPENAI_API_KEY:-}" ]] && printf 'OPENAI_API_KEY=%q\n' "$OPENAI_API_KEY"
    [[ -n "${ANTHROPIC_API_KEY:-}" ]] && printf 'ANTHROPIC_API_KEY=%q\n' "$ANTHROPIC_API_KEY"
    [[ -n "${GEMINI_API_KEY:-}" ]] && printf 'GEMINI_API_KEY=%q\n' "$GEMINI_API_KEY"
    [[ -n "${OPENROUTER_API_KEY:-}" ]] && printf 'OPENROUTER_API_KEY=%q\n' "$OPENROUTER_API_KEY"
  } >"$tmp"
  chmod 600 "$tmp"
  if [[ -f "$service_env" ]] && cmp -s "$tmp" "$service_env"; then rm -f "$tmp"; else mv "$tmp" "$service_env"; fi
}

litellm_service_stop() { launchctl bootout "$(litellm_service_domain)/com.litellm.proxy" 2>/dev/null || true; }

litellm_service_start() {
  local domain plist bootstrap_ok=0 output
  LITELLM_SERVICE_ERROR=""
  domain="$(litellm_service_domain)"
  plist="$(litellm_service_plist)"
  [[ -f "$plist" ]] || {
    LITELLM_SERVICE_ERROR="LiteLLM plist missing: $plist"
    printf '%s\n' "$LITELLM_SERVICE_ERROR" >&2
    return 1
  }
  for _ in 1 2 3; do
    litellm_service_stop
    sleep 2
    if output="$(launchctl bootstrap "$domain" "$plist" 2>&1)" && launchctl print "$domain/com.litellm.proxy" >/dev/null 2>&1; then
      bootstrap_ok=1
      break
    fi
  done
  if [[ "$bootstrap_ok" != "1" ]]; then
    LITELLM_SERVICE_ERROR="Failed to bootstrap com.litellm.proxy after 3 attempts: $output"
    printf '%s\n' "$LITELLM_SERVICE_ERROR" >&2
    return 1
  fi
  launchctl kickstart -k "$domain/com.litellm.proxy" >/dev/null 2>&1 || true
  launchctl print "$domain/com.litellm.proxy" >/dev/null 2>&1
}

litellm_service_restart() { litellm_service_start; }
