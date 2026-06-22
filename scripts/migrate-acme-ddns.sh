#!/usr/bin/env bash
set -euo pipefail

_SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
LIB_DIR="$SCRIPT_DIR/lib"

source "$LIB_DIR/common.sh"
source "$LIB_DIR/env.sh"

load_env || warn "$HOME/.env not found, skipping env load"

YES_MODE=0
if [[ "${1:-}" == "--yes" ]]; then
  YES_MODE=1
fi

if [[ "${DOTFILES_RUN_CADDY_SETUP:-0}" != "1" ]]; then
  info "DOTFILES_RUN_CADDY_SETUP='${DOTFILES_RUN_CADDY_SETUP:-0}' — skipping acme/ddns migration"
  exit 0
fi

# Privileged operations (LaunchDaemon, /etc, dscl) require root.
if [[ "$(id -u)" -ne 0 ]]; then
  info "Re-launching with sudo for privileged operations (LaunchDaemon, /etc, dscl)..."
  exec sudo "$0" "$@"
fi

info "This will decommission the existing dedicated-user acme.sh + ddns-route53 setup."
info "  • Stop old LaunchDaemons"
info "  • Archive /etc/acmesh and /etc/ddns-route53 to \$HOME"
info "  • Delete acme (gid 802) and ddnsr53 (gid 801) users/groups"
if [[ "$YES_MODE" -eq 1 ]] || [[ ! -t 0 ]]; then
  info "Non-interactive mode — proceeding with migration"
else
  read -r -p "Proceed with migration? (yes/no): " CONFIRM || {
    warn "Migration cancelled."
    exit 0
  }
  if [[ "$CONFIRM" != "yes" ]]; then
    info "Migration cancelled."
    exit 0
  fi
fi

# Resolve the real user's HOME (we're root now via sudo)
REAL_USER="${SUDO_USER:-$USER}"
REAL_HOME="$(eval echo "~$REAL_USER")"

declare -a SUMMARY_LINES=()
ARCHIVE_TS="$(date +%s)"

if [[ -f /Library/LaunchDaemons/com.crazy-max.ddns-route53.plist ]]; then
  info "Stopping com.crazy-max.ddns-route53 launch daemon..."
  step_status=0
  # Use bootout (modern); fall back to unload for older macOS
  launchctl bootout system /Library/LaunchDaemons/com.crazy-max.ddns-route53.plist 2>/dev/null ||
    launchctl unload /Library/LaunchDaemons/com.crazy-max.ddns-route53.plist 2>/dev/null || {
    warn "Failed to stop com.crazy-max.ddns-route53 launch daemon."
    step_status=1
  }
  rm -f /Library/LaunchDaemons/com.crazy-max.ddns-route53.plist || {
    warn "Failed to remove /Library/LaunchDaemons/com.crazy-max.ddns-route53.plist."
    step_status=1
  }
  if [[ "$step_status" -eq 0 ]]; then
    SUMMARY_LINES+=("Stopped and removed com.crazy-max.ddns-route53 launch daemon")
  fi
else
  ok "No com.crazy-max.ddns-route53 launch daemon found."
fi

if pgrep -u ddnsr53 ddns-route53 >/dev/null 2>&1; then
  info "Killing ddns-route53 processes owned by ddnsr53..."
  step_status=0
  pkill -u ddnsr53 -f ddns-route53 || {
    warn "Failed to kill ddns-route53 processes owned by ddnsr53."
    step_status=1
  }
  if [[ "$step_status" -eq 0 ]]; then
    SUMMARY_LINES+=("Stopped ddns-route53 processes for ddnsr53")
  fi
else
  ok "No ddns-route53 processes owned by ddnsr53 found."
fi

if crontab -u acme -l >/dev/null 2>&1; then
  info "Removing acme user crontab..."
  step_status=0
  crontab -u acme -r || {
    warn "Failed to remove acme user crontab."
    step_status=1
  }
  if [[ "$step_status" -eq 0 ]]; then
    SUMMARY_LINES+=("Removed acme user crontab")
  fi
else
  ok "No acme user crontab found."
fi

if [[ -d /etc/acmesh ]]; then
  info "Archiving /etc/acmesh..."
  step_status=0
  ARCHIVE_DEST="$REAL_HOME/.acmesh-archive-$ARCHIVE_TS"
  cp -a /etc/acmesh "$ARCHIVE_DEST" && rm -rf /etc/acmesh || {
    warn "Failed to archive /etc/acmesh."
    step_status=1
  }
  chown -R "$REAL_USER" "$ARCHIVE_DEST" 2>/dev/null || true
  if [[ "$step_status" -eq 0 ]]; then
    SUMMARY_LINES+=("Archived /etc/acmesh to $ARCHIVE_DEST")
  fi
else
  ok "No /etc/acmesh directory found."
fi

if [[ -d /etc/ddns-route53 ]]; then
  info "Archiving /etc/ddns-route53..."
  step_status=0
  ARCHIVE_DEST="$REAL_HOME/.ddns-route53-archive-$ARCHIVE_TS"
  cp -a /etc/ddns-route53 "$ARCHIVE_DEST" && rm -rf /etc/ddns-route53 || {
    warn "Failed to archive /etc/ddns-route53."
    step_status=1
  }
  chown -R "$REAL_USER" "$ARCHIVE_DEST" 2>/dev/null || true
  if [[ "$step_status" -eq 0 ]]; then
    SUMMARY_LINES+=("Archived /etc/ddns-route53 to $ARCHIVE_DEST")
  fi
else
  ok "No /etc/ddns-route53 directory found."
fi

if dscl . -read /Groups/acme >/dev/null 2>&1; then
  info "Deleting acme group (gid 802)..."
  step_status=0
  dscl . -delete /Groups/acme || {
    warn "Failed to delete /Groups/acme."
    step_status=1
  }
  if [[ "$step_status" -eq 0 ]]; then
    SUMMARY_LINES+=("Deleted acme group (gid 802)")
  fi
else
  ok "No acme group found."
fi

if dscl . -read /Users/acme >/dev/null 2>&1; then
  info "Deleting acme user..."
  step_status=0
  dscl . -delete /Users/acme || {
    warn "Failed to delete /Users/acme."
    step_status=1
  }
  if [[ "$step_status" -eq 0 ]]; then
    SUMMARY_LINES+=("Deleted acme user")
  fi
else
  ok "No acme user found."
fi

if dscl . -read /Groups/ddnsr53 >/dev/null 2>&1; then
  info "Deleting ddnsr53 group (gid 801)..."
  step_status=0
  dscl . -delete /Groups/ddnsr53 || {
    warn "Failed to delete /Groups/ddnsr53."
    step_status=1
  }
  if [[ "$step_status" -eq 0 ]]; then
    SUMMARY_LINES+=("Deleted ddnsr53 group (gid 801)")
  fi
else
  ok "No ddnsr53 group found."
fi

if dscl . -read /Users/ddnsr53 >/dev/null 2>&1; then
  info "Deleting ddnsr53 user..."
  step_status=0
  dscl . -delete /Users/ddnsr53 || {
    warn "Failed to delete /Users/ddnsr53."
    step_status=1
  }
  if [[ "$step_status" -eq 0 ]]; then
    SUMMARY_LINES+=("Deleted ddnsr53 user")
  fi
else
  ok "No ddnsr53 user found."
fi

# --- v1→v2: Migrate single-zone ddns to multi-zone ---
info "Checking for v1 single-zone ddns-route53 setup..."

OLD_DDNS_CONFIG="$HOME/.config/ddns-route53/ddns-route53.yml"
OLD_DDNS_PLIST="$HOME/Library/LaunchAgents/com.crazymax.ddns-route53.plist"

# Unload old single-zone LaunchAgent
if launchctl print "gui/$UID/com.crazymax.ddns-route53" &>/dev/null; then
  info "Unloading old single-zone ddns-route53 LaunchAgent..."
  launchctl bootout "gui/$UID/com.crazymax.ddns-route53" 2>/dev/null || launchctl unload -w "$OLD_DDNS_PLIST" 2>/dev/null || warn "Failed to unload old ddns-route53"
  ok "Old ddns-route53 LaunchAgent unloaded."
fi

# Archive old single-zone config
if [[ -f "$OLD_DDNS_CONFIG" ]]; then
  ARCHIVE_DIR="$HOME/.ddns-route53-v1-archive-$(date +%s)"
  info "Archiving old ddns config to ${ARCHIVE_DIR}..."
  mkdir -p "$ARCHIVE_DIR"
  cp -a "$OLD_DDNS_CONFIG" "$ARCHIVE_DIR/" || warn "Failed to archive old ddns config"
  rm -f "$OLD_DDNS_CONFIG" || warn "Failed to remove old ddns config"
  chown -R "$REAL_USER" "$ARCHIVE_DIR" 2>/dev/null || true
  ok "Old ddns config archived to ${ARCHIVE_DIR}"
fi

# Remove old single-zone plist
if [[ -f "$OLD_DDNS_PLIST" ]]; then
  rm -f "$OLD_DDNS_PLIST" || warn "Failed to remove old ddns plist"
  ok "Old ddns plist removed."
fi

info "Run 'make migrate' to update env vars for v2."

if ((${#SUMMARY_LINES[@]} > 0)); then
  ok "Migration complete.\n\nSummary:\n$(printf '  • %s\n' "${SUMMARY_LINES[@]}")"
else
  ok "Migration complete. No dedicated-user acme/ddns artifacts were found."
fi
