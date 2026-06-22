#!/usr/bin/env bash

# shellcheck disable=SC1091
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

caddy_get_bind_ip() {
  local ip
  ip="$(ipconfig getifaddr en0 2>/dev/null || true)"
  echo "${ip:-0.0.0.0}"
}

caddy_validate() {
  local caddyfile="${1:-$(brew --prefix)/etc/caddy/Caddyfile}"
  if caddy validate --config "$caddyfile" 2>/dev/null; then
    ok "Caddyfile validation passed: $caddyfile"
    return 0
  else
    warn "Caddyfile validation failed: $caddyfile"
    return 1
  fi
}

caddy_reload() {
  local caddyfile="${1:-$(brew --prefix)/etc/caddy/Caddyfile}"
  if caddy reload --force --config "$caddyfile" 2>/dev/null; then
    ok "Caddy reloaded: $caddyfile"
  else
    warn "Caddy reload failed: $caddyfile"
  fi
}

caddy_launch_agents_dir() {
  printf '%s\n' "$HOME/Library/LaunchAgents"
}

caddy_launchctl_domain() {
  printf 'gui/%s\n' "$UID"
}
