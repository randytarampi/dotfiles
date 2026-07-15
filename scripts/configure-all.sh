#!/usr/bin/env bash
set -euo pipefail

# configure-all.sh — Run all AI tool configure scripts in dependency order.
# Called by `make deploy` (after chezmoi apply) and `make configure`.
# Each step is gated on its DOTFILES_RUN_*_SETUP env var and warn-on-fail (never abort).

_SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
LIB_DIR="$SCRIPT_DIR/lib"

source "$LIB_DIR/common.sh"
source "$LIB_DIR/env.sh"
source "$LIB_DIR/tier_detect.sh"
source "$LIB_DIR/tier_args.sh"

# Load environment for API keys and gate vars
load_env || warn "\$HOME/.env not found, skipping env load"

info "Running full configuration pass..."

# Detect tiers (detect_tier sets $TIER, not the env var itself)
detect_tier DOTFILES_OPENCODE_TIER
OC_TIER="$TIER"
build_tier_extra_args
OC_ARGS=("${TIER_EXTRA_ARGS[@]}")

detect_tier DOTFILES_SMALLCODE_TIER
SC_TIER="$TIER"
build_tier_extra_args
SC_ARGS=("${TIER_EXTRA_ARGS[@]}")

# 1. Secrets/env distribution (configure-secrets.py writes .env to AI tool dirs)
#    Bridges the gap left by run_onchange_14-configure-secrets (which can't hash ~/.env).
if [[ "${DOTFILES_RUN_SECRETS_SETUP:-0}" == "1" ]]; then
  info "Configuring secrets and AI tool .env files..."
  python3 "$SCRIPT_DIR/configure-secrets.py" || warn "configure-secrets.py failed"
else
  info "DOTFILES_RUN_SECRETS_SETUP not set — skipping secrets distribution"
fi

# 1.5. Refresh Junie model profiles (models-only; dir scaffolding handled by run_onchange_06)
if [[ "${DOTFILES_RUN_JUNIE_CLI_SETUP:-0}" == "1" ]]; then
  info "Refreshing Junie model profiles (DOTFILES_RUN_JUNIE_CLI_SETUP=1)..."
  if python3 "$SCRIPT_DIR/configure-jetbrains-ai.py" --models; then
    ok "Junie model profiles refreshed"
  else
    warn "Junie model profile refresh failed"
  fi
else
  info "DOTFILES_RUN_JUNIE_CLI_SETUP='${DOTFILES_RUN_JUNIE_CLI_SETUP:-0}' — skipping junie models refresh"
fi

# 2. MCP config (must run before opencode — opencode calls configure-mcp-tool.py)
if [[ "${DOTFILES_RUN_MCP_SETUP:-0}" == "1" ]]; then
  info "Configuring MCP servers..."
  python3 "$SCRIPT_DIR/configure-mcps.py" --mode global --no-backup || warn "MCP config failed"
else
  info "DOTFILES_RUN_MCP_SETUP not set — skipping MCP configuration"
fi

# 3. OpenCode config (tier, models, voice — creates oh-my-opencode-slim.json)
if [[ "${DOTFILES_RUN_OPENCODE_SETUP:-0}" == "1" ]]; then
  info "Configuring OpenCode (tier=$OC_TIER)..."
  python3 "$SCRIPT_DIR/configure-opencode.py" --preset "$OC_TIER" ${OC_ARGS[@]+"${OC_ARGS[@]}"} || warn "OpenCode config failed"
else
  info "DOTFILES_RUN_OPENCODE_SETUP not set — skipping OpenCode configuration"
fi

# 3.5. Configure Meridian proxy plugin (must run after OpenCode; injects plugin into opencode.json)
if [[ "${DOTFILES_RUN_MERIDIAN_SETUP:-0}" == "1" ]]; then
  info "Configuring Meridian plugin (DOTFILES_RUN_MERIDIAN_SETUP=1)..."
  if python3 "$SCRIPT_DIR/configure-meridian.py"; then
    ok "Meridian configured"
  else
    warn "Meridian plugin config failed"
  fi
else
  info "DOTFILES_RUN_MERIDIAN_SETUP='${DOTFILES_RUN_MERIDIAN_SETUP:-0}' — skipping meridian"
fi

# 4. Mozart router
if [[ "${DOTFILES_RUN_MOZART_SETUP:-0}" == "1" ]]; then
  info "Configuring Mozart router..."
  python3 "$SCRIPT_DIR/configure-mozart-router.py" || warn "Mozart config failed"
else
  info "DOTFILES_RUN_MOZART_SETUP not set — skipping Mozart configuration"
fi

# 5. SmallCode config (reads oh-my-opencode-slim.json — must run after OpenCode)
if [[ "${DOTFILES_RUN_SMALLCODE_SETUP:-0}" == "1" ]]; then
  if command -v smallcode >/dev/null 2>&1; then
    info "Configuring SmallCode (tier=$SC_TIER)..."
    python3 "$SCRIPT_DIR/configure-smallcode.py" --preset "$SC_TIER" ${SC_ARGS[@]+"${SC_ARGS[@]}"} || warn "SmallCode config failed"
  else
    warn "smallcode CLI not found — skipping SmallCode configuration"
  fi
else
  info "DOTFILES_RUN_SMALLCODE_SETUP not set — skipping SmallCode configuration"
fi

# 7. Agent guidance distribution (writes ~/AGENTS.md and 6 other agent files)
if [[ "${DOTFILES_RUN_AGENT_GUIDANCE_SETUP:-0}" == "1" ]]; then
  info "Distributing agent guidance..."
  python3 "$SCRIPT_DIR/configure-agent-guidance.py" || warn "Agent guidance failed"
else
  info "DOTFILES_RUN_AGENT_GUIDANCE_SETUP not set — skipping agent guidance distribution"
fi

# 7.5. CodeGraph MCP config (modifies opencode.json — must run after OpenCode)
#      Runs AFTER agent guidance (step 7) so that codegraph install's marker-fenced
#      CODEGRAPH guidance block in agent files is not clobbered by configure-agent-guidance.py.
if [[ "${DOTFILES_RUN_CODEGRAPH_SETUP:-0}" == "1" ]]; then
  if command -v codegraph >/dev/null 2>&1; then
    info "Configuring CodeGraph MCP..."
    codegraph install -y --target auto --location global || warn "CodeGraph config failed"
  else
    warn "codegraph CLI not found — skipping CodeGraph MCP configuration"
  fi
else
  info "DOTFILES_RUN_CODEGRAPH_SETUP not set — skipping CodeGraph configuration"
fi

# 7.6. CodeGraph per-project indexes (batch init .codegraph/ in git repos under ~/Development)
#      Runs AFTER agent guidance (step 7) and codegraph install (step 7.5), so that
#      the marker-fenced CODEGRAPH guidance block is already preserved in agent files.
if [[ "${DOTFILES_RUN_CODEGRAPH_INDEX_SETUP:-0}" == "1" ]]; then
  if command -v codegraph >/dev/null 2>&1; then
    info "Indexing CodeGraph for git repos..."
    python3 "$SCRIPT_DIR/configure-codegraph.py" || warn "CodeGraph indexing failed"
  else
    warn "codegraph CLI not found — skipping CodeGraph indexing"
  fi
else
  info "DOTFILES_RUN_CODEGRAPH_INDEX_SETUP not set — skipping CodeGraph indexing"
fi

# 7.7. Ollama daemon env config (launchctl/systemd/setx — applies OLLAMA_* tuning vars)
if [[ "${DOTFILES_RUN_OLLAMA_DAEMON_SETUP:-0}" == "1" ]]; then
  info "Configuring Ollama daemon env vars (DOTFILES_RUN_OLLAMA_DAEMON_SETUP=1)..."
  # This is a chezmoi script (run_onchange_27), not a Python configure script.
  # configure-all.sh doesn't re-run it — chezmoi apply handles it.
  # This step exists in configure-all.sh for documentation/consistency.
  ok "Ollama daemon env config handled by chezmoi (run_onchange_27)"
else
  info "DOTFILES_RUN_OLLAMA_DAEMON_SETUP='${DOTFILES_RUN_OLLAMA_DAEMON_SETUP:-0}' — skipping Ollama daemon env config"
fi

# 7b. Skills distribution (manifest-driven reconcile via `skills` CLI + symlinks to all agent dirs)
if [[ "${DOTFILES_RUN_SKILLS_SETUP:-0}" == "1" ]]; then
  info "Distributing skills..."
  python3 "$SCRIPT_DIR/configure-skills.py" || warn "Skills distribution failed"
else
  info "DOTFILES_RUN_SKILLS_SETUP not set — skipping skills distribution"
fi

# 8. Caddy config (LAN exposure front door)
if [[ "${DOTFILES_RUN_CADDY_SETUP:-0}" == "1" ]]; then
  info "Configuring ddns-route53..."
  python3 "$SCRIPT_DIR/configure-ddns.py" || warn "configure-ddns.py failed"
  info "Configuring Caddy..."
  python3 "$SCRIPT_DIR/configure-caddy.py" || warn "Caddy config generation failed"
else
  info "DOTFILES_RUN_CADDY_SETUP not set — skipping Caddy configuration"
fi

# 9. Restart OpenCode Web to pick up config changes (opencode.json, acp-agents.json, etc.)
if [[ "${DOTFILES_RUN_OPENCODE_SETUP:-0}" == "1" ]]; then
  info "Restarting OpenCode Web to pick up config changes..."
  if [[ "$(uname)" == "Darwin" ]]; then
    launchctl bootout "gui/$(id -u)/com.opencode.web" 2>/dev/null || true
    sleep 1
    launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.opencode.web.plist 2>/dev/null || true
    launchctl kickstart -k "gui/$(id -u)/com.opencode.web" 2>/dev/null || true
  else
    systemctl --user restart opencode-web 2>/dev/null || true
  fi
  ok "OpenCode Web restarted."
fi

ok "Configuration complete!"
