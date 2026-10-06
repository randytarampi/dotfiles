#!/usr/bin/env bash

litellm_service_domain() { printf 'gui/%s\n' "${UID:-$(id -u)}"; }
litellm_service_plist() { printf '%s\n' "$HOME/Library/LaunchAgents/com.litellm.proxy.plist"; }
litellm_require_database_url() {
  [[ -n "${LITELLM_DATABASE_URL:-}${DATABASE_URL:-}" ]] || die "LITELLM_DATABASE_URL is required when LiteLLM is enabled"
}

litellm_prisma_schema_matches() {
  local venv="$1" source_schema="$2" packaged_schema="$3" temp_dir formatted result=1
  [[ -f "$packaged_schema" ]] || return 1
  temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/dotfiles-prisma.XXXXXX")" || return 1
  formatted="$temp_dir/schema.prisma"
  if cp "$source_schema" "$formatted" &&
    PATH="$venv/bin:$PATH" "$venv/bin/prisma" format --schema "$formatted" >/dev/null 2>&1 &&
    cmp -s "$formatted" "$packaged_schema"; then
    result=0
  fi
  rm -f "$formatted"
  rmdir "$temp_dir" 2>/dev/null || true
  return "$result"
}

litellm_ensure_prisma_client() {
  local venv="${1:-}" python prisma schema packaged_schema
  python="$venv/bin/python"
  prisma="$venv/bin/prisma"
  if [[ ! -x "$python" ]]; then
    printf 'LiteLLM venv Python is unavailable for Prisma client check\n' >&2
    return 1
  fi
  schema="$("$python" -c 'import site; from pathlib import Path; print(next((str(path) for root in site.getsitepackages() for path in [Path(root) / "litellm_proxy_extras" / "schema.prisma"] if path.is_file()), ""))' 2>/dev/null)" || schema=""
  if [[ -z "$schema" || ! -f "$schema" ]]; then
    printf 'LiteLLM Prisma schema is unavailable; refusing to start\n' >&2
    return 1
  fi
  if [[ ! -x "$prisma" ]]; then
    printf 'LiteLLM Prisma CLI is unavailable; refusing to start\n' >&2
    return 1
  fi
  packaged_schema="$(dirname "$(dirname "$schema")")/prisma/schema.prisma"
  if "$python" -c 'from prisma import Prisma' >/dev/null 2>&1 &&
    litellm_prisma_schema_matches "$venv" "$schema" "$packaged_schema"; then
    return 0
  fi
  if ! PATH="$venv/bin:$PATH" "$prisma" generate --schema "$schema" >/dev/null 2>&1; then
    printf 'LiteLLM Prisma client generation failed; refusing to start\n' >&2
    return 1
  fi
  if ! "$python" -c 'from prisma import Prisma' >/dev/null 2>&1; then
    printf 'LiteLLM Prisma client remains unavailable after generation; refusing to start\n' >&2
    return 1
  fi
  if ! litellm_prisma_schema_matches "$venv" "$schema" "$packaged_schema"; then
    printf 'LiteLLM Prisma client schema still mismatches after generation; refusing to start\n' >&2
    return 1
  fi
}

litellm_service_env_sync() {
  local service_env="${1:-$HOME/.local/share/litellm/service.env}" tmp
  local LITELLM_MASTER_KEY="" LITELLM_PORT="" DISABLE_ADMIN_UI="" DATABASE_URL="" LITELLM_DATABASE_URL=""
  local LITELLM_OPENCODE_KEY="" LITELLM_PI_KEY="" LITELLM_OPENWEBUI_KEY="" LITELLM_JUNIE_KEY=""
  local GITHUB_COPILOT_TOKEN_DIR="" GITHUB_COPILOT_ACCESS_TOKEN_FILE="" GITHUB_COPILOT_API_KEY_FILE="" CHATGPT_TOKEN_DIR="" CHATGPT_AUTH_FILE=""
  local config_path
  config_path="$(dirname "$service_env")/config.yaml"
  # Provider credentials are exactly what the generated config references via
  # os.environ/<KEY>, so new providers need no edits here. Before the first
  # config write, fall back to the bootstrap set captured historically.
  local -a provider_keys=()
  if [[ -f "$config_path" ]]; then
    local ref
    for ref in $(
      grep -o 'os\.environ/[A-Z][A-Z0-9_]*' "$config_path" 2>/dev/null |
        sed 's|os\.environ/||' | sort -u
    ); do
      case "$ref" in
      LITELLM_MASTER_KEY | LITELLM_PORT | DISABLE_ADMIN_UI | DATABASE_URL) continue ;;
      LITELLM_OPENCODE_KEY | LITELLM_PI_KEY | LITELLM_OPENWEBUI_KEY | LITELLM_JUNIE_KEY) continue ;;
      *) provider_keys+=("$ref") ;;
      esac
    done
  fi
  if [[ ! -f "$config_path" ]]; then
    provider_keys=(
      OMLX_API_KEY MERIDIAN_API_KEY OPENAI_API_KEY ANTHROPIC_API_KEY
      GEMINI_API_KEY OPENROUTER_API_KEY OPENCODE_API_KEY OLLAMA_API_KEY
    )
  fi
  mkdir -p "$(dirname "$service_env")"
  # Provider names are discovered from config.yaml; shadow each one locally
  # before sourcing either env file so values cannot persist in caller scope.
  local key
  for key in ${provider_keys[@]+"${provider_keys[@]}"}; do
    case "$key" in
    [A-Z][A-Z0-9_]*) local "$key" ;;
    *) continue ;;
    esac
  done
  local env_LITELLM_MASTER_KEY="" env_LITELLM_PORT="" env_DISABLE_ADMIN_UI="" env_DATABASE_URL="" env_LITELLM_DATABASE_URL=""
  local env_GITHUB_COPILOT_TOKEN_DIR="" env_GITHUB_COPILOT_ACCESS_TOKEN_FILE="" env_GITHUB_COPILOT_API_KEY_FILE="" env_CHATGPT_TOKEN_DIR="" env_CHATGPT_AUTH_FILE=""
  if [[ -f "$HOME/.env" ]]; then
    # shellcheck disable=SC1091
    source "$HOME/.env"
  fi
  env_LITELLM_MASTER_KEY="$LITELLM_MASTER_KEY"
  env_LITELLM_PORT="$LITELLM_PORT"
  env_DISABLE_ADMIN_UI="$DISABLE_ADMIN_UI"
  env_DATABASE_URL="$DATABASE_URL"
  env_LITELLM_DATABASE_URL="$LITELLM_DATABASE_URL"
  env_GITHUB_COPILOT_TOKEN_DIR="$GITHUB_COPILOT_TOKEN_DIR"
  env_GITHUB_COPILOT_ACCESS_TOKEN_FILE="$GITHUB_COPILOT_ACCESS_TOKEN_FILE"
  env_GITHUB_COPILOT_API_KEY_FILE="$GITHUB_COPILOT_API_KEY_FILE"
  env_CHATGPT_TOKEN_DIR="$CHATGPT_TOKEN_DIR"
  env_CHATGPT_AUTH_FILE="$CHATGPT_AUTH_FILE"
  # Capture provider credentials from ~/.env, then clear them so values
  # sourced from service.env below can never win; the captured HOME values
  # are restored afterwards.
  for key in ${provider_keys[@]+"${provider_keys[@]}"}; do
    case "$key" in
    [A-Z][A-Z0-9_]*) ;;
    *) continue ;;
    esac
    eval "local env_${key}=\"\${${key}-}\""
    unset "$key"
  done
  unset GITHUB_COPILOT_TOKEN_DIR GITHUB_COPILOT_ACCESS_TOKEN_FILE GITHUB_COPILOT_API_KEY_FILE CHATGPT_TOKEN_DIR CHATGPT_AUTH_FILE
  if [[ -f "$service_env" ]]; then
    # shellcheck disable=SC1090
    source "$service_env"
  fi
  for key in ${provider_keys[@]+"${provider_keys[@]}"}; do
    case "$key" in
    [A-Z][A-Z0-9_]*) unset "$key" ;;
    *) continue ;;
    esac
  done
  if [[ -n "$env_LITELLM_MASTER_KEY" ]]; then LITELLM_MASTER_KEY="$env_LITELLM_MASTER_KEY"; fi
  if [[ -n "$env_LITELLM_PORT" ]]; then LITELLM_PORT="$env_LITELLM_PORT"; fi
  if [[ -n "$env_DISABLE_ADMIN_UI" ]]; then DISABLE_ADMIN_UI="$env_DISABLE_ADMIN_UI"; fi
  if [[ -n "$env_DATABASE_URL" ]]; then DATABASE_URL="$env_DATABASE_URL"; fi
  if [[ -n "$env_LITELLM_DATABASE_URL" ]]; then DATABASE_URL="$env_LITELLM_DATABASE_URL"; fi
  GITHUB_COPILOT_TOKEN_DIR="$env_GITHUB_COPILOT_TOKEN_DIR"
  GITHUB_COPILOT_ACCESS_TOKEN_FILE="$env_GITHUB_COPILOT_ACCESS_TOKEN_FILE"
  GITHUB_COPILOT_API_KEY_FILE="$env_GITHUB_COPILOT_API_KEY_FILE"
  CHATGPT_TOKEN_DIR="$env_CHATGPT_TOKEN_DIR"
  CHATGPT_AUTH_FILE="$env_CHATGPT_AUTH_FILE"
  for key in ${provider_keys[@]+"${provider_keys[@]}"}; do
    case "$key" in
    [A-Z][A-Z0-9_]*) ;;
    *) continue ;;
    esac
    eval "if [[ -n \"\$env_${key}\" ]]; then ${key}=\"\$env_${key}\"; fi"
  done
  LITELLM_PORT="${LITELLM_PORT:-4000}"
  # DB-free deployments cannot complete a UI login (it mints a DB-backed
  # session key), so the Admin UI is disabled by default to avoid a
  # confusing "Not connected to DB!" login error. The default always resets
  # to True unless LITELLM_DISABLE_ADMIN_UI overrides it. Set
  # LITELLM_DISABLE_ADMIN_UI=False in ~/.env to re-enable it.
  if [[ -n "${LITELLM_DISABLE_ADMIN_UI:-}" ]]; then
    DISABLE_ADMIN_UI="$LITELLM_DISABLE_ADMIN_UI"
  else
    DISABLE_ADMIN_UI="True"
  fi
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
    printf 'DISABLE_ADMIN_UI=%q\n' "$DISABLE_ADMIN_UI"
    [[ -n "$GITHUB_COPILOT_TOKEN_DIR" ]] && printf 'GITHUB_COPILOT_TOKEN_DIR=%q\n' "$GITHUB_COPILOT_TOKEN_DIR"
    [[ -n "$GITHUB_COPILOT_ACCESS_TOKEN_FILE" ]] && printf 'GITHUB_COPILOT_ACCESS_TOKEN_FILE=%q\n' "$GITHUB_COPILOT_ACCESS_TOKEN_FILE"
    [[ -n "$GITHUB_COPILOT_API_KEY_FILE" ]] && printf 'GITHUB_COPILOT_API_KEY_FILE=%q\n' "$GITHUB_COPILOT_API_KEY_FILE"
    [[ -n "$CHATGPT_TOKEN_DIR" ]] && printf 'CHATGPT_TOKEN_DIR=%q\n' "$CHATGPT_TOKEN_DIR"
    [[ -n "$CHATGPT_AUTH_FILE" ]] && printf 'CHATGPT_AUTH_FILE=%q\n' "$CHATGPT_AUTH_FILE"
    if [[ -n "$DATABASE_URL" ]]; then printf 'DATABASE_URL=%q\n' "$DATABASE_URL"; fi
    for key in ${provider_keys[@]+"${provider_keys[@]}"}; do
      case "$key" in
      [A-Z][A-Z0-9_]*) ;;
      *) continue ;;
      esac
      eval "if [[ -n \"\${${key}-}\" ]]; then printf '${key}=%q\\n' \"\${${key}}\"; fi"
    done
    [[ -n "${LITELLM_OPENCODE_KEY:-}" ]] && printf 'LITELLM_OPENCODE_KEY=%q\n' "$LITELLM_OPENCODE_KEY"
    [[ -n "${LITELLM_PI_KEY:-}" ]] && printf 'LITELLM_PI_KEY=%q\n' "$LITELLM_PI_KEY"
    [[ -n "${LITELLM_OPENWEBUI_KEY:-}" ]] && printf 'LITELLM_OPENWEBUI_KEY=%q\n' "$LITELLM_OPENWEBUI_KEY"
    [[ -n "${LITELLM_JUNIE_KEY:-}" ]] && printf 'LITELLM_JUNIE_KEY=%q\n' "$LITELLM_JUNIE_KEY"
  } >"$tmp"
  chmod 600 "$tmp"
  if [[ -f "$service_env" ]] && cmp -s "$tmp" "$service_env"; then
    chmod 600 "$service_env"
    rm -f "$tmp"
  else mv "$tmp" "$service_env"; fi
}

litellm_service_stop() {
  local plist
  plist="$(litellm_service_plist)"
  [[ -f "$plist" ]] || return 0
  launchctl bootout "$(litellm_service_domain)/com.litellm.proxy" 2>/dev/null || true
}

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
