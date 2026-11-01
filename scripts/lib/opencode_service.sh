#!/usr/bin/env bash

# Shared OpenCode web LaunchAgent lifecycle. launchd is the sole owner of the
# com.opencode.web unit (plist written by script 26; configure-all.sh and the
# Makefile bootout/bootstrap/kickstart the same label). The HTTP probe is the
# v2 /api/info contract from scripts/lib/opencode_web.py.

opencode_service_stop() {
  launchctl bootout "$1/com.opencode.web" >/dev/null 2>&1 || true
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
  local port="${1:-${OPENCODE_SERVER_PORT:-4096}}" attempt outcome=0
  if ! [[ "${port}" =~ ^[0-9]+$ ]] || ((port < 1 || port > 65535)); then
    printf 'OpenCode web health probe failed: invalid port value (must be 1–65535)\n' >&2
    return 1
  fi
  if [[ -z "${OPENCODE_PASSWORD:-${OPENCODE_SERVER_PASSWORD:-}}" ]]; then
    printf 'OpenCode web health probe failed: no server password is configured\n' >&2
    return 1
  fi
  for attempt in {1..15}; do
    outcome=0
    OPENCODE_PASSWORD="${OPENCODE_PASSWORD:-${OPENCODE_SERVER_PASSWORD:-}}" \
      python3 "$(dirname "${BASH_SOURCE[0]}")/opencode_web.py" "$port" \
      >/dev/null 2>&1 || outcome=$?
    if [[ "$outcome" -eq 0 ]]; then
      printf 'OpenCode web HTTP health ready (/api/info, attempt %s)\n' "$attempt"
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
