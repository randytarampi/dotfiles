#!/usr/bin/env bash

# Shared OpenCode web LaunchAgent lifecycle. Handles launchd's asynchronous
# bootout/bootstrap race and keeps loaded and HTTP-ready as separate phases.

opencode_service_stop() {
  local domain="$1"
  launchctl bootout "$domain/com.opencode.web" >/dev/null 2>&1 || true
}

opencode_service_bootstrap() {
  local domain="$1" plist="$2" output="" attempt
  for attempt in 1 2 3; do
    opencode_service_stop "$domain"
    sleep 2
    if output="$(launchctl bootstrap "$domain" "$plist" 2>&1)" &&
      launchctl print "$domain/com.opencode.web" >/dev/null 2>&1; then
      printf 'OpenCode web LaunchAgent loaded (attempt %s)\n' "$attempt"
      return 0
    fi
  done
  printf 'OpenCode web LaunchAgent failed after 3 bootstrap attempts: %s\n' "$output" >&2
  return 1
}

opencode_service_wait_healthy() {
  local port="${1:-${OPENCODE_SERVER_PORT:-4096}}" attempt
  for attempt in {1..15}; do
    if curl -fsS --max-time 3 "http://127.0.0.1:${port}/global/health" >/dev/null 2>&1; then
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
  opencode_service_bootstrap "$domain" "$plist" || return 1
  opencode_service_wait_healthy "$port"
}
