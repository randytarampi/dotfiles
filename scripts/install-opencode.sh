#!/usr/bin/env bash
set -euo pipefail

_SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"

# ─── install-opencode.sh ──────────────────────────────────────────────────────
#
# Installs OpenCode plugins and tools. Run once per machine.
# Does NOT write any configuration files — use configure-opencode.py for that.
#
# What this installs:
#   1. oh-my-opencode-slim (agent orchestration plugin)
#   2. @plannotator/opencode (plan annotation plugin)
#   3. @tarquinen/opencode-dcp (conversation compression plugin)
#   4. plannotator CLI (for slash commands)
#   5. Ollama Cloud model metadata (pulled locally for OpenCode discovery)
# ───────────────────────────────────────────────────────────────────────────────

source "$SCRIPT_DIR/lib/common.sh"

REQUIRE_CMD="${REQUIRE_CMD:-1}"

require_cmd() {
  if ! command -v "$1" &>/dev/null; then
    err "'$1' not found. Install it first: $2"
    if [ "$REQUIRE_CMD" -eq 1 ]; then exit 1; else return 1; fi
  fi
}

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------
info "Checking prerequisites..."
require_cmd opencode "https://opencode.ai"
require_cmd ollama "https://ollama.com"
require_cmd git "https://git-scm.com"

if ! command -v bun &>/dev/null && ! command -v npx &>/dev/null; then
  err "Need 'bun' or 'npx'. Install one: https://bun.sh or https://nodejs.org"
  exit 1
fi

# ---------------------------------------------------------------------------
# 1. Install oh-my-opencode-slim
# ---------------------------------------------------------------------------
info "Installing oh-my-opencode-slim..."
if command -v bun &>/dev/null; then
  bunx oh-my-opencode-slim@latest install
else
  npx oh-my-opencode-slim@latest install
fi
ok "oh-my-opencode-slim installed"

# ---------------------------------------------------------------------------
# 2. Install plannotator + DCP plugins
# ---------------------------------------------------------------------------
info "Installing @plannotator/opencode..."
opencode plugin @plannotator/opencode@latest --global
ok "plannotator plugin installed"

info "Installing @tarquinen/opencode-dcp..."
opencode plugin @tarquinen/opencode-dcp@latest --global
ok "DCP plugin installed"

# ---------------------------------------------------------------------------
# 3. Install vibeguard (sensitive-string redaction plugin)
# ---------------------------------------------------------------------------
info "Installing opencode-vibeguard..."
opencode plugin opencode-vibeguard --global
ok "vibeguard plugin installed"

# ---------------------------------------------------------------------------
# 4. Install plannotator CLI (for /plannotator-review slash commands)
#     (was step 3 before vibeguard was added)
# ---------------------------------------------------------------------------
info "Installing plannotator CLI..."
curl -fsSL https://plannotator.ai/install.sh | bash
ok "plannotator CLI installed"

# ---------------------------------------------------------------------------
# 5. Pull Ollama Cloud model metadata locally
#   (Required by OpenCode to recognize cloud models)
#   Reads model names from configs/opencode/ollama-cloud-models.json
# ---------------------------------------------------------------------------
info "Pulling Ollama Cloud model metadata..."
CONFIG_FILE="$SCRIPT_DIR/../configs/opencode/ollama-cloud-models.json"
if [ -f "$CONFIG_FILE" ]; then
  while IFS= read -r model; do
    ollama pull "${model}:cloud" 2>/dev/null || warn "Could not pull '${model}:cloud' — you may need 'ollama login' first"
  done < <(python3 -c "import json; data=json.load(open('$CONFIG_FILE')); [print(m) for m in data['models']]" 2>/dev/null)
else
  warn "ollama-cloud-models.json not found at $CONFIG_FILE — skipping cloud model pull"
fi
ok "Ollama Cloud model metadata pulled"

# ---------------------------------------------------------------------------
# Done!
# ---------------------------------------------------------------------------
info "OpenCode tools installed!

Next steps:
  1. Write config:       configure-opencode.py
  2. Authenticate:       opencode auth login
     → Select 'OpenAI' (ChatGPT Plus/Pro)
     → Select 'Ollama Cloud' (API key from https://ollama.com/settings/keys)
  3. Refresh models:     opencode models --refresh

Install script complete!"
