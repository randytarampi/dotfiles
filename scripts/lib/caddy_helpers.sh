#!/usr/bin/env bash

# shellcheck disable=SC1091
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

caddy_get_bind_ip() {
  local ip
  if [[ "$(uname -s)" == "Darwin" ]]; then
    ip="$(ipconfig getifaddr en0 2>/dev/null || true)"
  else
    # Linux: try hostname -I (first IP), then ip addr
    ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
    if [[ -z "$ip" ]]; then
      ip="$(ip -4 addr show 2>/dev/null | grep -oP 'inet \K[\d.]+' | grep -v '127.0.0.1' | head -1 || true)"
    fi
  fi
  echo "${ip:-0.0.0.0}"
}

caddy_prefix() {
  if command -v brew >/dev/null 2>&1; then
    brew --prefix
  elif [[ "$(uname -s)" == "Linux" ]]; then
    echo "/usr"
  else
    echo ""
  fi
}

caddy_validate() {
  local caddyfile="${1:-$(caddy_prefix)/etc/caddy/Caddyfile}"
  if caddy validate --config "$caddyfile" 2>/dev/null; then
    ok "Caddyfile validation passed: $caddyfile"
    return 0
  else
    warn "Caddyfile validation failed: $caddyfile"
    return 1
  fi
}

caddy_reload() {
  local caddyfile="${1:-$(caddy_prefix)/etc/caddy/Caddyfile}"
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
