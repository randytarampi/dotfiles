#!/usr/bin/env bash

# Shared Open WebUI LaunchAgent lifecycle. Callers own provisioning and health
# policy; these helpers own the launchd bootout/bootstrap race.

openwebui_service_domain() {
  printf 'gui/%s\n' "${UID:-$(id -u)}"
}

openwebui_service_plist() {
  printf '%s\n' "${HOME}/Library/LaunchAgents/com.openwebui.web.plist"
}

openwebui_service_env_sync() {
  local service_env="${1:-$HOME/.local/share/openwebui/service.env}"
  local service_home
  local WEBUI_SECRET_KEY="" WEBUI_ADMIN_EMAIL="" WEBUI_ADMIN_PASSWORD=""
  local OPENWEBUI_API_KEY="" OPENWEBUI_PORT=""
  local tmp
  service_home="$(dirname "$service_env")"
  mkdir -p "$service_home"
  local env_WEBUI_SECRET_KEY="" env_WEBUI_ADMIN_EMAIL="" env_WEBUI_ADMIN_PASSWORD="" env_OPENWEBUI_API_KEY="" env_OPENWEBUI_PORT=""
  if [[ -f "$HOME/.env" ]]; then
    # shellcheck disable=SC1091
    source "$HOME/.env"
  fi
  env_WEBUI_SECRET_KEY="$WEBUI_SECRET_KEY"
  env_WEBUI_ADMIN_EMAIL="$WEBUI_ADMIN_EMAIL"
  env_WEBUI_ADMIN_PASSWORD="$WEBUI_ADMIN_PASSWORD"
  env_OPENWEBUI_API_KEY="$OPENWEBUI_API_KEY"
  env_OPENWEBUI_PORT="$OPENWEBUI_PORT"
  if [[ -f "$service_env" ]]; then
    # shellcheck disable=SC1090
    source "$service_env"
  fi
  [[ -n "$env_WEBUI_SECRET_KEY" ]] && WEBUI_SECRET_KEY="$env_WEBUI_SECRET_KEY"
  [[ -n "$env_WEBUI_ADMIN_EMAIL" ]] && WEBUI_ADMIN_EMAIL="$env_WEBUI_ADMIN_EMAIL"
  [[ -n "$env_WEBUI_ADMIN_PASSWORD" ]] && WEBUI_ADMIN_PASSWORD="$env_WEBUI_ADMIN_PASSWORD"
  [[ -n "$env_OPENWEBUI_API_KEY" ]] && OPENWEBUI_API_KEY="$env_OPENWEBUI_API_KEY"
  unset OPENWEBUI_PORT
  [[ -n "$env_OPENWEBUI_PORT" ]] && OPENWEBUI_PORT="$env_OPENWEBUI_PORT"
  WEBUI_SECRET_KEY="${WEBUI_SECRET_KEY:-}"
  WEBUI_ADMIN_EMAIL="${WEBUI_ADMIN_EMAIL:-admin@localhost}"
  WEBUI_ADMIN_PASSWORD="${WEBUI_ADMIN_PASSWORD:-}"
  OPENWEBUI_API_KEY="${OPENWEBUI_API_KEY:-}"
  OPENWEBUI_PORT="${OPENWEBUI_PORT:-8080}"
  if [[ -z "$WEBUI_SECRET_KEY" || -z "$WEBUI_ADMIN_PASSWORD" ]]; then
    command -v openssl >/dev/null 2>&1 || return 1
    if [[ -z "$WEBUI_SECRET_KEY" ]]; then
      WEBUI_SECRET_KEY="$(openssl rand -hex 32)" || return 1
      [[ -n "$WEBUI_SECRET_KEY" ]] || return 1
    fi
    if [[ -z "$WEBUI_ADMIN_PASSWORD" ]]; then
      WEBUI_ADMIN_PASSWORD="$(openssl rand -hex 32)" || return 1
      [[ -n "$WEBUI_ADMIN_PASSWORD" ]] || return 1
    fi
  fi
  tmp="${service_env}.tmp.$$"
  umask 077
  {
    printf 'WEBUI_SECRET_KEY=%q\n' "$WEBUI_SECRET_KEY"
    printf 'WEBUI_ADMIN_EMAIL=%q\n' "$WEBUI_ADMIN_EMAIL"
    printf 'WEBUI_ADMIN_PASSWORD=%q\n' "$WEBUI_ADMIN_PASSWORD"
    printf 'OPENWEBUI_API_KEY=%q\n' "$OPENWEBUI_API_KEY"
    printf 'OPENWEBUI_PORT=%q\n' "$OPENWEBUI_PORT"
  } >"$tmp"
  chmod 600 "$tmp"
  if [[ -f "$service_env" ]] && cmp -s "$tmp" "$service_env"; then
    rm -f "$tmp"
  else
    mv "$tmp" "$service_env"
  fi
}

openwebui_service_stop() {
  launchctl bootout "$(openwebui_service_domain)/com.openwebui.web" 2>/dev/null || true
}

openwebui_service_start() {
  local domain plist bootstrap_ok=0 output
  OPENWEBUI_SERVICE_ERROR=""
  domain="$(openwebui_service_domain)"
  plist="$(openwebui_service_plist)"
  [[ -f "$plist" ]] || {
    OPENWEBUI_SERVICE_ERROR="Open WebUI plist missing: $plist"
    printf '%s\n' "$OPENWEBUI_SERVICE_ERROR" >&2
    return 1
  }
  for _ in 1 2 3; do
    openwebui_service_stop
    sleep 2
    if output="$(launchctl bootstrap "$domain" "$plist" 2>&1)" && launchctl print "$domain/com.openwebui.web" >/dev/null 2>&1; then
      bootstrap_ok=1
      break
    fi
  done
  if [[ "$bootstrap_ok" != "1" ]]; then
    OPENWEBUI_SERVICE_ERROR="Failed to bootstrap com.openwebui.web after 3 attempts: $output"
    printf '%s\n' "$OPENWEBUI_SERVICE_ERROR" >&2
    return 1
  fi
  launchctl kickstart -k "$domain/com.openwebui.web" >/dev/null 2>&1 || true
  launchctl print "$domain/com.openwebui.web" >/dev/null 2>&1
}

openwebui_service_restart() {
  openwebui_service_start
}

openwebui_terminal_service_domain() {
  openwebui_service_domain
}

openwebui_terminal_service_plist() {
  printf '%s\n' "${HOME}/Library/LaunchAgents/com.openwebui.terminal.plist"
}

openwebui_terminal_service_env_sync() {
  local service_env="${1:-$HOME/.local/share/openwebui/terminal.env}"
  local terminal_home
  local OPEN_TERMINAL_API_KEY="" OPEN_TERMINAL_FILE_BROWSER_ROOT="" OPENWEBUI_TERMINAL_PORT=""
  local tmp
  terminal_home="$(dirname "$service_env")"
  mkdir -p "$terminal_home"
  local env_WEBUI_SECRET_KEY="" env_WEBUI_ADMIN_EMAIL="" env_WEBUI_ADMIN_PASSWORD="" env_OPENWEBUI_API_KEY="" env_OPENWEBUI_PORT=""
  if [[ -f "$HOME/.env" ]]; then
    # shellcheck disable=SC1091
    source "$HOME/.env"
  fi
  env_WEBUI_SECRET_KEY="$WEBUI_SECRET_KEY"
  env_WEBUI_ADMIN_EMAIL="$WEBUI_ADMIN_EMAIL"
  env_WEBUI_ADMIN_PASSWORD="$WEBUI_ADMIN_PASSWORD"
  env_OPENWEBUI_API_KEY="$OPENWEBUI_API_KEY"
  env_OPENWEBUI_PORT="$OPENWEBUI_PORT"
  if [[ -f "$service_env" ]]; then
    # shellcheck disable=SC1090
    source "$service_env"
  fi
  [[ -n "$env_WEBUI_SECRET_KEY" ]] && WEBUI_SECRET_KEY="$env_WEBUI_SECRET_KEY"
  [[ -n "$env_WEBUI_ADMIN_EMAIL" ]] && WEBUI_ADMIN_EMAIL="$env_WEBUI_ADMIN_EMAIL"
  [[ -n "$env_WEBUI_ADMIN_PASSWORD" ]] && WEBUI_ADMIN_PASSWORD="$env_WEBUI_ADMIN_PASSWORD"
  [[ -n "$env_OPENWEBUI_API_KEY" ]] && OPENWEBUI_API_KEY="$env_OPENWEBUI_API_KEY"
  unset OPENWEBUI_PORT
  [[ -n "$env_OPENWEBUI_PORT" ]] && OPENWEBUI_PORT="$env_OPENWEBUI_PORT"
  OPEN_TERMINAL_FILE_BROWSER_ROOT="${OPEN_TERMINAL_FILE_BROWSER_ROOT:-$HOME/.local/share/openwebui/terminal-workspace}"
  OPENWEBUI_TERMINAL_PORT="${OPENWEBUI_TERMINAL_PORT:-8123}"
  if [[ -z "${OPEN_TERMINAL_API_KEY:-}" ]]; then
    command -v openssl >/dev/null 2>&1 || return 1
    if ! OPEN_TERMINAL_API_KEY="$(openssl rand -hex 32)" || [[ -z "$OPEN_TERMINAL_API_KEY" ]]; then
      return 1
    fi
  fi
  tmp="${service_env}.tmp.$$"
  umask 077
  {
    printf 'OPEN_TERMINAL_API_KEY=%q\n' "$OPEN_TERMINAL_API_KEY"
    printf 'OPEN_TERMINAL_FILE_BROWSER_ROOT=%q\n' "$OPEN_TERMINAL_FILE_BROWSER_ROOT"
    printf 'OPENWEBUI_TERMINAL_PORT=%q\n' "$OPENWEBUI_TERMINAL_PORT"
  } >"$tmp"
  chmod 600 "$tmp"
  if [[ -f "$service_env" ]] && cmp -s "$tmp" "$service_env"; then
    rm -f "$tmp"
  else
    mv "$tmp" "$service_env"
  fi
}

openwebui_terminal_service_stop() {
  launchctl bootout "$(openwebui_terminal_service_domain)/com.openwebui.terminal" 2>/dev/null || true
}

openwebui_terminal_service_start() {
  local domain plist bootstrap_ok=0 output
  OPENWEBUI_TERMINAL_SERVICE_ERROR=""
  domain="$(openwebui_terminal_service_domain)"
  plist="$(openwebui_terminal_service_plist)"
  [[ -f "$plist" ]] || {
    OPENWEBUI_TERMINAL_SERVICE_ERROR="Open Terminal plist missing: $plist"
    printf '%s\n' "$OPENWEBUI_TERMINAL_SERVICE_ERROR" >&2
    return 1
  }
  for _ in 1 2 3; do
    openwebui_terminal_service_stop
    sleep 2
    if output="$(launchctl bootstrap "$domain" "$plist" 2>&1)" && launchctl print "$domain/com.openwebui.terminal" >/dev/null 2>&1; then
      bootstrap_ok=1
      break
    fi
  done
  if [[ "$bootstrap_ok" != "1" ]]; then
    OPENWEBUI_TERMINAL_SERVICE_ERROR="Failed to bootstrap com.openwebui.terminal after 3 attempts: $output"
    printf '%s\n' "$OPENWEBUI_TERMINAL_SERVICE_ERROR" >&2
    return 1
  fi
  launchctl kickstart -k "$domain/com.openwebui.terminal" >/dev/null 2>&1 || true
  launchctl print "$domain/com.openwebui.terminal" >/dev/null 2>&1
}

openwebui_terminal_service_restart() {
  openwebui_terminal_service_start
}

openwebui_computer_service_domain() {
  openwebui_service_domain
}

openwebui_computer_service_plist() {
  printf '%s\n' "${HOME}/Library/LaunchAgents/com.openwebui.computer.plist"
}

openwebui_computer_service_env_sync() {
  local service_env="${1:-$HOME/.local/share/cptr/service.env}"
  local cptr_home
  local CPTR_DATA_DIR="" OPENWEBUI_COMPUTER_PORT=""
  local tmp
  cptr_home="$(dirname "$service_env")"
  mkdir -p "$cptr_home"
  if [[ -f "$service_env" ]]; then
    # shellcheck disable=SC1090
    source "$service_env"
  fi
  unset OPENWEBUI_COMPUTER_PORT
  if [[ -f "$HOME/.env" ]]; then
    # shellcheck disable=SC1091
    source "$HOME/.env"
  fi
  # Invariant: cptr state stays isolated from Open WebUI and arbitrary env overrides.
  CPTR_DATA_DIR="$HOME/.local/share/cptr/data"
  OPENWEBUI_COMPUTER_PORT="${OPENWEBUI_COMPUTER_PORT:-8124}"
  tmp="${service_env}.tmp.$$"
  umask 077
  {
    printf 'CPTR_DATA_DIR=%q\n' "$CPTR_DATA_DIR"
    printf 'OPENWEBUI_COMPUTER_PORT=%q\n' "$OPENWEBUI_COMPUTER_PORT"
  } >"$tmp"
  chmod 600 "$tmp"
  if [[ -f "$service_env" ]] && cmp -s "$tmp" "$service_env"; then
    rm -f "$tmp"
  else
    mv "$tmp" "$service_env"
  fi
}

openwebui_computer_service_stop() {
  launchctl bootout "$(openwebui_computer_service_domain)/com.openwebui.computer" 2>/dev/null || true
}

openwebui_computer_service_start() {
  local domain plist bootstrap_ok=0 output
  OPENWEBUI_COMPUTER_SERVICE_ERROR=""
  domain="$(openwebui_computer_service_domain)"
  plist="$(openwebui_computer_service_plist)"
  [[ -f "$plist" ]] || {
    OPENWEBUI_COMPUTER_SERVICE_ERROR="Open WebUI Computer plist missing: $plist"
    printf '%s\n' "$OPENWEBUI_COMPUTER_SERVICE_ERROR" >&2
    return 1
  }
  for _ in 1 2 3; do
    openwebui_computer_service_stop
    sleep 2
    if output="$(launchctl bootstrap "$domain" "$plist" 2>&1)" && launchctl print "$domain/com.openwebui.computer" >/dev/null 2>&1; then
      bootstrap_ok=1
      break
    fi
  done
  if [[ "$bootstrap_ok" != "1" ]]; then
    OPENWEBUI_COMPUTER_SERVICE_ERROR="Failed to bootstrap com.openwebui.computer after 3 attempts: $output"
    printf '%s\n' "$OPENWEBUI_COMPUTER_SERVICE_ERROR" >&2
    return 1
  fi
  launchctl kickstart -k "$domain/com.openwebui.computer" >/dev/null 2>&1 || true
  launchctl print "$domain/com.openwebui.computer" >/dev/null 2>&1
}

openwebui_computer_service_restart() {
  openwebui_computer_service_start
}
