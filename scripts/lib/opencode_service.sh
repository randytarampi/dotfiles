#!/usr/bin/env bash

# Shared OpenCode v2 service lifecycle, plus LaunchAgent compatibility helpers.

opencode_service_stop() {
  opencode service stop
}

opencode_service_bootstrap() {
  local domain="$1" plist="$2" port="${3:-${OPENCODE_SERVER_PORT:-4096}}"
  : "$domain" "$plist"
  opencode service set port "$port" || return 1
  opencode service restart
}

opencode_service_wait_healthy() {
  local port="${1:-${OPENCODE_SERVER_PORT:-4096}}" attempt password="${OPENCODE_PASSWORD:-${OPENCODE_SERVER_PASSWORD:-}}"
  if [[ -z "$password" ]]; then
    printf 'OpenCode web health probe skipped: no OPENCODE_PASSWORD/OPENCODE_SERVER_PASSWORD configured (service start itself succeeded)\n' >&2
    return 0
  fi
  for attempt in {1..15}; do
    if python3 "$(dirname "${BASH_SOURCE[0]}")/opencode_web.py" "$port" "$password" >/dev/null 2>&1; then
      printf 'OpenCode web HTTP health ready (attempt %s)\n' "$attempt"
      return 0
    fi
    sleep 2
  done
  printf 'OpenCode web HTTP health did not become ready after 30 seconds\n' >&2
  return 1
}

opencode_service_start() {
  local domain="$1" plist="$2" port="${3:-${OPENCODE_SERVER_PORT:-4096}}"
  : "$domain" "$plist"
  opencode service set port "$port" || return 1
  opencode service restart || return 1
  opencode_service_wait_healthy "$port"
}

opencode_service_status() {
  opencode service status
}
