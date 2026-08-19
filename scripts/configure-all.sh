#!/usr/bin/env bash
set -euo pipefail

# configure-all.sh — Run all AI tool configure scripts in dependency order.
# Called by `make deploy` (after chezmoi apply) and `make configure`.
# Each step is gated on its DOTFILES_RUN_*_SETUP env var.

_SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
LIB_DIR="$SCRIPT_DIR/lib"

source "$LIB_DIR/common.sh"
source "$LIB_DIR/common_args.sh"

export COMMON_USAGE="$0 [options]"
export COMMON_HELP_TEXT="Run all AI tool configure scripts in dependency order.

Available flags:
  --help       Show this help message
  --dry-run    Preview changes without writing to the filesystem
  --no-backup  Disable backup file creation

Each step is gated on its DOTFILES_RUN_*_SETUP environment variable."
COMMON_STRICT=1 parse_common_args "$@"

source "$LIB_DIR/env.sh"
source "$LIB_DIR/tier_detect.sh"
source "$LIB_DIR/tier_args.sh"

FAILURES=0

run_step() {
  local description="$1"
  shift
  if "$@"; then
    ok "$description"
  else
    warn "$description failed"
    FAILURES=$((FAILURES + 1))
  fi
  return 0
}

# Load environment for API keys and gate vars
load_env || warn "\$HOME/.env not found, skipping env load"

# Clean up stale files from chezmoi renames.
# When a chezmoi source file is renamed (e.g., dcp.json → dcp.jsonc),
# chezmoi writes the new file but leaves the old one. Remove known orphans.
info "Cleaning stale chezmoi targets..."

OPENCODE_CONFIG_DIR="$HOME/.config/opencode"

# dcp.json was renamed to dcp.jsonc in the chezmoi source.
# If both exist, dcp.jsonc takes precedence (DCP config.ts prefers .jsonc),
# but the stale dcp.json causes verify-config.py to report a shadowing error.
if [[ -f "$OPENCODE_CONFIG_DIR/dcp.json" && -f "$OPENCODE_CONFIG_DIR/dcp.jsonc" ]]; then
  if [[ "$COMMON_DRY_RUN" == "1" ]]; then
    info "Would remove stale $OPENCODE_CONFIG_DIR/dcp.json (dcp.jsonc exists)"
  else
    rm -f "$OPENCODE_CONFIG_DIR/dcp.json"
    ok "Removed stale dcp.json (superseded by dcp.jsonc)"
  fi
fi

# SmallCode was removed from the managed MCP registry. Remove its old config
# directory and stale server entries left behind by merge-based MCP writers.
SMALLCODE_CONFIG_DIR="$HOME/.config/smallcode"
if [[ -d "$SMALLCODE_CONFIG_DIR" ]]; then
  if [[ "$COMMON_DRY_RUN" == "1" ]]; then
    info "Would remove stale $SMALLCODE_CONFIG_DIR"
  else
    rm -rf "$SMALLCODE_CONFIG_DIR"
    ok "Removed stale SmallCode configuration directory"
  fi
fi

if [[ "$COMMON_DRY_RUN" == "1" ]]; then
  info "Would remove stale SmallCode MCP entries from generated tool configs"
else
  python3 - "$HOME" <<'PY'
import json
import os
import sys

home = sys.argv[1]
roots = [
    os.path.join(home, ".ai"),
    os.path.join(home, ".codex"),
    os.path.join(home, ".cursor"),
    os.path.join(home, ".gemini"),
    os.path.join(home, ".config", "opencode"),
]

def remove_smallcode(value):
    changed = False
    if isinstance(value, dict):
        for key in list(value):
            if key.lower() == "smallcode":
                del value[key]
                changed = True
            else:
                changed = remove_smallcode(value[key]) or changed
    elif isinstance(value, list):
        for item in value:
            changed = remove_smallcode(item) or changed
    return changed

for root in roots:
    if not os.path.isdir(root):
        continue
    for directory, _, filenames in os.walk(root):
        for filename in filenames:
            if not filename.endswith((".json", ".jsonc")):
                continue
            path = os.path.join(directory, filename)
            try:
                with open(path, encoding="utf-8") as handle:
                    data = json.load(handle)
                if remove_smallcode(data):
                    with open(path, "w", encoding="utf-8") as handle:
                        json.dump(data, handle, indent=2)
                        handle.write("\n")
                    print(f"Removed stale SmallCode MCP entries from {path}")
            except (OSError, ValueError):
                continue
PY
fi

info "Running full configuration pass..."

# Detect tiers (detect_tier sets $TIER, not the env var itself)
detect_tier DOTFILES_OPENCODE_TIER
OC_TIER="$TIER"
build_tier_extra_args
OC_ARGS=(${COMMON_FORWARD_ARGS[@]+"${COMMON_FORWARD_ARGS[@]}"} ${TIER_EXTRA_ARGS[@]+"${TIER_EXTRA_ARGS[@]}"})

# 0.5. Reconcile npm "..." entries from the base Brewfile into the active nvm node.
#       Runs after chezmoi apply (nvm should be active on PATH here). warn-on-fail.
if [[ "$COMMON_DRY_RUN" == "1" ]]; then
  info "Skipping npm package reconciliation (dry-run mode)"
elif [[ "${DOTFILES_RUN_PACKAGES_SETUP:-0}" == "1" ]]; then
  info "Reconciling npm packages from base Brewfile into active node..."
  run_step "npm package reconciliation" bash "$SCRIPT_DIR/install-npm-brewfile-packages.sh" "$SCRIPT_DIR/../Brewfile"
else
  info "DOTFILES_RUN_PACKAGES_SETUP not set — skipping npm package reconciliation"
fi

# 1. Secrets/env distribution (configure-secrets.py writes .env to AI tool dirs)
#    Bridges the gap left by run_onchange_14-configure-secrets (which can't hash ~/.env).
if [[ "$COMMON_DRY_RUN" == "1" ]]; then
  info "Skipping secrets distribution (dry-run mode)"
elif [[ "${DOTFILES_RUN_SECRETS_SETUP:-0}" == "1" ]]; then
  info "Configuring secrets and AI tool .env files..."
  run_step "Secrets configuration" python3 "$SCRIPT_DIR/configure-secrets.py"
else
  info "DOTFILES_RUN_SECRETS_SETUP not set — skipping secrets distribution"
fi

# 1.5. Refresh Junie model profiles (models-only; dir scaffolding handled by run_onchange_06)
if [[ "$COMMON_DRY_RUN" == "1" ]]; then
  info "Skipping Junie model refresh (dry-run mode)"
elif [[ "${DOTFILES_RUN_JUNIE_CLI_SETUP:-0}" == "1" ]]; then
  info "Refreshing Junie model profiles (DOTFILES_RUN_JUNIE_CLI_SETUP=1)..."
  run_step "Junie model profiles refreshed" python3 "$SCRIPT_DIR/configure-jetbrains-ai.py" --models
else
  info "DOTFILES_RUN_JUNIE_CLI_SETUP='${DOTFILES_RUN_JUNIE_CLI_SETUP:-0}' — skipping junie models refresh"
fi

# 2. MCP config (must run before opencode — opencode calls configure-mcp-tool.py)
if [[ "${DOTFILES_RUN_MCP_SETUP:-0}" == "1" ]]; then
  info "Configuring MCP servers..."
  run_step "MCP configuration" python3 "$SCRIPT_DIR/configure-mcps.py" --mode global ${COMMON_FORWARD_ARGS[@]+"${COMMON_FORWARD_ARGS[@]}"}
else
  info "DOTFILES_RUN_MCP_SETUP not set — skipping MCP configuration"
fi

# Track OpenCode success separately to gate the service restart.
OC_SUCCESS=0

# 3. OpenCode config (tier, models, voice — creates oh-my-opencode-slim.json)
if [[ "${DOTFILES_RUN_OPENCODE_SETUP:-0}" == "1" ]]; then
  info "Configuring OpenCode (tier=$OC_TIER)..."
  _failures_before="$FAILURES"
  run_step "OpenCode configuration" python3 "$SCRIPT_DIR/configure-opencode.py" --preset "$OC_TIER" "${OC_ARGS[@]}"
  if [[ "$FAILURES" -eq "$_failures_before" ]]; then
    OC_SUCCESS=1
  fi
else
  info "DOTFILES_RUN_OPENCODE_SETUP not set — skipping OpenCode configuration"
fi

# 3.5. Configure Meridian proxy plugin (must run after OpenCode; injects plugin into opencode.json)
if [[ "$COMMON_DRY_RUN" == "1" ]]; then
  info "Skipping Meridian configuration (dry-run mode)"
elif [[ "${DOTFILES_RUN_MERIDIAN_SETUP:-0}" == "1" ]]; then
  info "Configuring Meridian plugin (DOTFILES_RUN_MERIDIAN_SETUP=1)..."
  run_step "Meridian configuration" python3 "$SCRIPT_DIR/configure-meridian.py"
else
  info "DOTFILES_RUN_MERIDIAN_SETUP='${DOTFILES_RUN_MERIDIAN_SETUP:-0}' — skipping meridian"
fi

# 3.6. Configure Codex CLI provider (must run after MCP config which also writes to config.toml)
if [[ "$COMMON_DRY_RUN" == "1" ]]; then
  info "Skipping Codex configuration (dry-run mode)"
elif [[ "${DOTFILES_RUN_MERIDIAN_SETUP:-0}" == "1" ]]; then
  info "Configuring Codex CLI provider (DOTFILES_RUN_MERIDIAN_SETUP=1)..."
  run_step "Codex configuration" python3 "$SCRIPT_DIR/configure-codex.py"
else
  info "DOTFILES_RUN_MERIDIAN_SETUP not set — skipping Codex configuration"
fi

# 4. Mozart router
if [[ "$COMMON_DRY_RUN" == "1" ]]; then
  info "Skipping Mozart configuration (dry-run mode)"
elif [[ "${DOTFILES_RUN_MOZART_SETUP:-0}" == "1" ]]; then
  info "Configuring Mozart router..."
  run_step "Mozart configuration" python3 "$SCRIPT_DIR/configure-mozart-router.py"
else
  info "DOTFILES_RUN_MOZART_SETUP not set — skipping Mozart configuration"
fi

# 7. Agent guidance distribution (writes ~/AGENTS.md and 6 other agent files)
if [[ "${DOTFILES_RUN_AGENT_GUIDANCE_SETUP:-0}" == "1" ]]; then
  info "Distributing agent guidance..."
  if [[ "$COMMON_DRY_RUN" == "1" ]]; then
    run_step "Agent guidance" python3 "$SCRIPT_DIR/configure-agent-guidance.py" --dry-run
  else
    run_step "Agent guidance" python3 "$SCRIPT_DIR/configure-agent-guidance.py"
  fi
else
  info "DOTFILES_RUN_AGENT_GUIDANCE_SETUP not set — skipping agent guidance distribution"
fi

# 7.5. CodeGraph MCP config (modifies opencode.json — must run after OpenCode)
#      Runs AFTER agent guidance (step 7) so that codegraph install's marker-fenced
#      CODEGRAPH guidance block in agent files is not clobbered by configure-agent-guidance.py.
if [[ "${DOTFILES_RUN_CODEGRAPH_SETUP:-0}" == "1" ]]; then
  if command -v codegraph >/dev/null 2>&1; then
    if [[ "$COMMON_DRY_RUN" == "1" ]]; then
      info "Skipping CodeGraph configuration (dry-run mode)"
    else
      info "Configuring CodeGraph MCP..."
      run_step "CodeGraph configuration" codegraph install -y --target auto --location global
    fi
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
    if [[ "$COMMON_DRY_RUN" == "1" ]]; then
      run_step "CodeGraph indexing" python3 "$SCRIPT_DIR/configure-codegraph.py" --dry-run
    else
      run_step "CodeGraph indexing" python3 "$SCRIPT_DIR/configure-codegraph.py"
    fi
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
  if [[ "$COMMON_DRY_RUN" == "1" ]]; then
    run_step "Skills distribution" python3 "$SCRIPT_DIR/configure-skills.py" --dry-run
  else
    run_step "Skills distribution" python3 "$SCRIPT_DIR/configure-skills.py"
  fi
else
  info "DOTFILES_RUN_SKILLS_SETUP not set — skipping skills distribution"
fi

# 8a. DDNS
if [[ "$COMMON_DRY_RUN" == "1" ]]; then
  info "Skipping DDNS configuration (dry-run mode)"
elif [[ "${DOTFILES_RUN_DDNS_SETUP:-${DOTFILES_RUN_CADDY_SETUP:-0}}" == "1" ]]; then
  info "Configuring ddns-route53..."
  run_step "ddns-route53 configuration" python3 "$SCRIPT_DIR/configure-ddns.py"
fi

# 8b. Caddy config (LAN exposure front door)
if [[ "$COMMON_DRY_RUN" == "1" ]]; then
  info "Skipping Caddy configuration (dry-run mode)"
elif [[ "${DOTFILES_RUN_CADDY_SETUP:-0}" == "1" ]]; then
  info "Configuring Caddy..."
  run_step "Caddy configuration" python3 "$SCRIPT_DIR/configure-caddy.py" ${COMMON_FORWARD_ARGS[@]+"${COMMON_FORWARD_ARGS[@]}"}
fi

# 9. Restart OpenCode Web to pick up config changes (opencode.json, acp-agents.json, etc.)
#     Only restart if the OpenCode config step succeeded.
if [[ "$COMMON_DRY_RUN" == "1" ]]; then
  info "Skipping service restart (dry-run mode)"
elif [[ "${DOTFILES_RUN_OPENCODE_SETUP:-0}" == "1" && "$OC_SUCCESS" == "1" ]]; then
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
elif [[ "${DOTFILES_RUN_OPENCODE_SETUP:-0}" == "1" && "$OC_SUCCESS" == "0" ]]; then
  warn "Skipping OpenCode Web restart (config step failed or was skipped)"
fi

if [[ "$FAILURES" -gt 0 ]]; then
  warn "Configuration complete with $FAILURES failures"
  exit 1
fi

ok "Configuration complete!"
exit 0
