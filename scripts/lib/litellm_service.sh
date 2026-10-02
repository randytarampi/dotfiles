#!/usr/bin/env bash

litellm_service_domain() { printf 'gui/%s\n' "${UID:-$(id -u)}"; }
litellm_service_plist() { printf '%s\n' "$HOME/Library/LaunchAgents/com.litellm.proxy.plist"; }
litellm_require_database_url() {
  [[ -n "${DATABASE_URL:-}" ]] || die "DATABASE_URL is required when LiteLLM is enabled"
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
  local LITELLM_MASTER_KEY="" LITELLM_PORT="" DISABLE_ADMIN_UI="" DATABASE_URL=""
  local OMLX_API_KEY="" MERIDIAN_API_KEY="" OPENAI_API_KEY="" ANTHROPIC_API_KEY="" GEMINI_API_KEY="" OPENROUTER_API_KEY="" OPENCODE_API_KEY="" OLLAMA_API_KEY=""
  local LITELLM_OPENCODE_KEY="" LITELLM_PI_KEY="" LITELLM_OPENWEBUI_KEY="" LITELLM_JUNIE_KEY=""
  mkdir -p "$(dirname "$service_env")"
  local env_LITELLM_MASTER_KEY="" env_LITELLM_PORT="" env_DISABLE_ADMIN_UI="" env_DATABASE_URL=""
  local env_OMLX_API_KEY="" env_MERIDIAN_API_KEY="" env_OPENAI_API_KEY="" env_ANTHROPIC_API_KEY="" env_GEMINI_API_KEY="" env_OPENROUTER_API_KEY="" env_OPENCODE_API_KEY="" env_OLLAMA_API_KEY=""
  if [[ -f "$HOME/.env" ]]; then
    # shellcheck disable=SC1091
    source "$HOME/.env"
  fi
  env_LITELLM_MASTER_KEY="$LITELLM_MASTER_KEY"
  env_LITELLM_PORT="$LITELLM_PORT"
  env_DISABLE_ADMIN_UI="$DISABLE_ADMIN_UI"
  env_DATABASE_URL="$DATABASE_URL"
  env_OMLX_API_KEY="$OMLX_API_KEY"
  env_MERIDIAN_API_KEY="$MERIDIAN_API_KEY"
  env_OPENAI_API_KEY="$OPENAI_API_KEY"
  env_ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY"
  env_GEMINI_API_KEY="$GEMINI_API_KEY"
  env_OPENROUTER_API_KEY="$OPENROUTER_API_KEY"
  env_OPENCODE_API_KEY="$OPENCODE_API_KEY"
  env_OLLAMA_API_KEY="$OLLAMA_API_KEY"
  if [[ -f "$service_env" ]]; then
    # shellcheck disable=SC1090
    source "$service_env"
  fi
  [[ -n "$env_LITELLM_MASTER_KEY" ]] && LITELLM_MASTER_KEY="$env_LITELLM_MASTER_KEY"
  [[ -n "$env_LITELLM_PORT" ]] && LITELLM_PORT="$env_LITELLM_PORT"
  [[ -n "$env_DISABLE_ADMIN_UI" ]] && DISABLE_ADMIN_UI="$env_DISABLE_ADMIN_UI"
  [[ -n "$env_DATABASE_URL" ]] && DATABASE_URL="$env_DATABASE_URL"
  [[ -n "$env_OMLX_API_KEY" ]] && OMLX_API_KEY="$env_OMLX_API_KEY"
  [[ -n "$env_MERIDIAN_API_KEY" ]] && MERIDIAN_API_KEY="$env_MERIDIAN_API_KEY"
  [[ -n "$env_OPENAI_API_KEY" ]] && OPENAI_API_KEY="$env_OPENAI_API_KEY"
  [[ -n "$env_ANTHROPIC_API_KEY" ]] && ANTHROPIC_API_KEY="$env_ANTHROPIC_API_KEY"
  [[ -n "$env_GEMINI_API_KEY" ]] && GEMINI_API_KEY="$env_GEMINI_API_KEY"
  [[ -n "$env_OPENROUTER_API_KEY" ]] && OPENROUTER_API_KEY="$env_OPENROUTER_API_KEY"
  [[ -n "$env_OPENCODE_API_KEY" ]] && OPENCODE_API_KEY="$env_OPENCODE_API_KEY"
  # Unset the service-file values FIRST so ~/.env-captured values win
  # below; then restore captured ~/.env values for keys whose os.environ/
  # references are present in the generated config (the unset loop removes
  # the rest). This keeps referenced provider keys in service.env.
  unset OMLX_API_KEY MERIDIAN_API_KEY OPENAI_API_KEY ANTHROPIC_API_KEY GEMINI_API_KEY OPENROUTER_API_KEY OPENCODE_API_KEY OLLAMA_API_KEY LITELLM_PORT
  [[ -n "$env_LITELLM_PORT" ]] && LITELLM_PORT="$env_LITELLM_PORT"
  [[ -n "$env_OMLX_API_KEY" ]] && OMLX_API_KEY="$env_OMLX_API_KEY"
  [[ -n "$env_MERIDIAN_API_KEY" ]] && MERIDIAN_API_KEY="$env_MERIDIAN_API_KEY"
  [[ -n "$env_OPENAI_API_KEY" ]] && OPENAI_API_KEY="$env_OPENAI_API_KEY"
  [[ -n "$env_ANTHROPIC_API_KEY" ]] && ANTHROPIC_API_KEY="$env_ANTHROPIC_API_KEY"
  [[ -n "$env_GEMINI_API_KEY" ]] && GEMINI_API_KEY="$env_GEMINI_API_KEY"
  [[ -n "$env_OPENROUTER_API_KEY" ]] && OPENROUTER_API_KEY="$env_OPENROUTER_API_KEY"
  [[ -n "$env_OPENCODE_API_KEY" ]] && OPENCODE_API_KEY="$env_OPENCODE_API_KEY"
  [[ -n "$env_OLLAMA_API_KEY" ]] && OLLAMA_API_KEY="$env_OLLAMA_API_KEY"
  local config_path
  config_path="$(dirname "$service_env")/config.yaml"
  if [[ -f "$config_path" ]]; then
    for provider_key in OMLX_API_KEY MERIDIAN_API_KEY OPENAI_API_KEY ANTHROPIC_API_KEY GEMINI_API_KEY OPENROUTER_API_KEY OPENCODE_API_KEY OLLAMA_API_KEY; do
      grep -Fq "os.environ/${provider_key}" "$config_path" || unset "$provider_key"
    done
  fi
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
    [[ -n "$DATABASE_URL" ]] && printf 'DATABASE_URL=%q\n' "$DATABASE_URL"
    [[ -n "${OMLX_API_KEY:-}" ]] && printf 'OMLX_API_KEY=%q\n' "$OMLX_API_KEY"
    [[ -n "${MERIDIAN_API_KEY:-}" ]] && printf 'MERIDIAN_API_KEY=%q\n' "$MERIDIAN_API_KEY"
    [[ -n "${OPENAI_API_KEY:-}" ]] && printf 'OPENAI_API_KEY=%q\n' "$OPENAI_API_KEY"
    [[ -n "${ANTHROPIC_API_KEY:-}" ]] && printf 'ANTHROPIC_API_KEY=%q\n' "$ANTHROPIC_API_KEY"
    [[ -n "${GEMINI_API_KEY:-}" ]] && printf 'GEMINI_API_KEY=%q\n' "$GEMINI_API_KEY"
    [[ -n "${OPENROUTER_API_KEY:-}" ]] && printf 'OPENROUTER_API_KEY=%q\n' "$OPENROUTER_API_KEY"
    [[ -n "${OPENCODE_API_KEY:-}" ]] && printf 'OPENCODE_API_KEY=%q\n' "$OPENCODE_API_KEY"
    [[ -n "${OLLAMA_API_KEY:-}" ]] && printf 'OLLAMA_API_KEY=%q\n' "$OLLAMA_API_KEY"
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
