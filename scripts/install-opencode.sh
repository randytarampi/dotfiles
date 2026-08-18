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
#   3b. @ramtinj95/opencode-tokenscope (token usage and cost analysis)
#   4. plannotator CLI (for slash commands)
#   5. Ollama Cloud model metadata (pulled locally for OpenCode discovery)
#   6. lazyskills CLI (skill discovery and management)
# ───────────────────────────────────────────────────────────────────────────────

# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/common.sh"
source "$SCRIPT_DIR/lib/common_args.sh"
export COMMON_USAGE="$0"
export COMMON_HELP_TEXT="Install OpenCode plugins and supporting tools."
parse_common_args "$@"
set -- ${COMMON_ARGS_REMAINING[@]+"${COMMON_ARGS_REMAINING[@]}"}

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
# Pipe 'n' to skip the "Star the repo on GitHub?" prompt
# --background-subagents=yes enables background subagents during install
if command -v bun &>/dev/null; then
  echo n | bunx oh-my-opencode-slim@latest install --background-subagents=yes
else
  echo n | npx oh-my-opencode-slim@latest install --background-subagents=yes
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
# 3b. Install opencode-tokenscope (token usage and cost analysis)
# ---------------------------------------------------------------------------
info "Installing @ramtinj95/opencode-tokenscope..."
opencode plugin @ramtinj95/opencode-tokenscope@latest --global
ok "opencode-tokenscope plugin installed"

# ---------------------------------------------------------------------------
# 4. Install plannotator CLI (for /plannotator-review slash commands)
#     (was step 3 before vibeguard was added)
# ---------------------------------------------------------------------------
info "Installing plannotator CLI..."
curl -fsSL https://plannotator.ai/install.sh | bash -s -- --no-extras --model-invocable none
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
# 5b. Remove stale Ollama Cloud model stubs no longer in the registry
#   (Cleans up retired models that were previously `ollama pull`ed as :cloud stubs)
# ---------------------------------------------------------------------------
info "Cleaning up stale Ollama Cloud model stubs..."
if command -v ollama &>/dev/null; then
  # Build the set of expected cloud model names from the registry (both :cloud and -cloud suffixes)
  expected_cloud=$(python3 -c "
import json
try:
    data = json.load(open('$CONFIG_FILE'))
    for m in data.get('models', {}):
        print(f'{m}-cloud')
        print(f'{m}:cloud')
except Exception:
    pass
" 2>/dev/null | sort -u)
  # List currently pulled cloud models and remove any not in expected set
  ollama list 2>/dev/null | tail -n +2 | awk '{print $1}' | grep -E '(:cloud|-cloud)$' | while read -r stale; do
    if ! echo "$expected_cloud" | grep -qxF "$stale"; then
      ollama rm "$stale" 2>/dev/null && info "Removed stale cloud stub: $stale" || warn "Could not remove '$stale'"
    fi
  done
fi
ok "Stale cloud model stub cleanup complete"

# ---------------------------------------------------------------------------
# 6. Install lazyskills CLI (skill discovery and management)
#    macOS: brew cask (alvinunreal/tap/lazyskills)
#    Linux: curl installer (https://lazyskills.sh/install)
#    Windows: PowerShell installer (irm https://lazyskills.sh/install.ps1 | iex)
# ---------------------------------------------------------------------------
info "Installing lazyskills CLI..."
if command -v lazyskills &>/dev/null; then
  ok "lazyskills CLI already installed"
elif [[ "$(uname -s)" == "Darwin" ]]; then
  brew tap alvinunreal/tap 2>/dev/null || true
  brew install --cask lazyskills || warn "brew install --cask lazyskills failed — see scripts/install-skills.sh"
elif command -v curl &>/dev/null; then
  curl -fsSL https://lazyskills.sh/install | sh || warn "lazyskills curl install failed"
else
  warn "Cannot install lazyskills — see scripts/install-skills.sh for manual instructions"
fi

# ---------------------------------------------------------------------------
# 7. Install CodeGraph CLI (local semantic code index + MCP server)
# ---------------------------------------------------------------------------
info "Installing @colbymchenry/codegraph globally..."
if command -v npm &>/dev/null; then
  if npm list -g @colbymchenry/codegraph &>/dev/null 2>&1; then
    ok "codegraph CLI already installed globally"
  else
    if npm install -g @colbymchenry/codegraph@latest 2>/dev/null; then
      ok "codegraph CLI installed globally"
    else
      warn "codegraph CLI install failed — you can install manually with: npm i -g @colbymchenry/codegraph"
    fi
  fi
else
  warn "npm not found; skipping codegraph CLI install"
fi

# Configure CodeGraph MCP for all detected agents (non-interactive, idempotent)
# This covers tools we don't manage in global-mcps.json (Claude Code, Hermes, Antigravity)
# plus confirms our managed tools are configured. Safe to re-run — shows "Unchanged" if
# codegraph is already in the agent's config.
if command -v codegraph &>/dev/null; then
  info "Configuring CodeGraph MCP for detected agents..."
  if codegraph install -y --target auto --location global 2>/dev/null; then
    ok "CodeGraph MCP configured for detected agents"
  else
    warn "codegraph install failed — you can run 'codegraph install' manually"
  fi
else
  warn "codegraph CLI not found; skipping agent MCP config"
fi
# Distribute home-level agent guidance to all agent files
# (reads configs/agents/home-agents.md, writes to ~/AGENTS.md and agent files)
# Runs even if codegraph CLI is missing — it distributes guidance, not codegraph config.
"$SCRIPT_DIR/configure-agent-guidance.py"

# ---------------------------------------------------------------------------
# 8. Install opencode-voice plugin (voice input/output for TUI)
# ---------------------------------------------------------------------------
info "Installing @renjfk/opencode-voice..."
opencode plugin @renjfk/opencode-voice@latest --global
ok "opencode-voice plugin installed"

# ---------------------------------------------------------------------------
# 8b. Install voice dependencies (whisper-cpp, sox, piper-tts) + models
#      Controlled by DOTFILES_RUN_VOICE_SETUP (default: 0 — skip)
#      Models: DOTFILES_WHISPER_MODEL (default: ggml-large-v3-turbo.bin)
#              DOTFILES_PIPER_VOICE (default: en_US-lessac-high)
# ---------------------------------------------------------------------------
VOICE_SETUP="${DOTFILES_RUN_VOICE_SETUP:-0}"
if [[ "$VOICE_SETUP" != "1" ]]; then
  info "DOTFILES_RUN_VOICE_SETUP='${VOICE_SETUP}' — skipping voice deps & models"
  info "  Set DOTFILES_RUN_VOICE_SETUP=1 to install whisper-cpp, sox, piper-tts, and models"
else
  info "Installing voice dependencies..."

  # ── whisper-cpp + sox (STT) ──────────────────────────────────────
  if command -v brew &>/dev/null; then
    for pkg in whisper-cpp sox; do
      if brew list "$pkg" &>/dev/null 2>&1; then
        ok "$pkg already installed"
      else
        if brew install "$pkg" 2>/dev/null; then
          ok "$pkg installed"
        else
          warn "$pkg install failed — install manually: brew install $pkg"
        fi
      fi
    done
  else
    warn "brew not found; skipping whisper-cpp/sox install"
  fi

  # ── Piper TTS ─────────────────────────────────────────────────────
  if command -v uv &>/dev/null; then
    if uv tool list 2>/dev/null | grep -q "piper-tts"; then
      ok "piper-tts already installed via uv"
    else
      if uv tool install piper-tts 2>/dev/null; then
        ok "piper-tts installed via uv"
      else
        warn "piper-tts install failed — install manually: uv tool install piper-tts"
      fi
    fi
  elif command -v pip &>/dev/null; then
    if pip show piper-tts &>/dev/null 2>&1; then
      ok "piper-tts already installed via pip"
    else
      if pip install piper-tts 2>/dev/null; then
        ok "piper-tts installed via pip"
      else
        warn "piper-tts install failed — install manually: pip install piper-tts"
      fi
    fi
  else
    warn "Neither uv nor pip found; skipping piper-tts install"
  fi

  # ── Download Whisper model ─────────────────────────────────────────
  WHISPER_MODEL="${DOTFILES_WHISPER_MODEL:-ggml-large-v3-turbo.bin}"
  WHISPER_DIR="$HOME/.local/share/whisper-cpp"
  WHISPER_PATH="$WHISPER_DIR/$WHISPER_MODEL"

  if [[ -f "$WHISPER_PATH" ]]; then
    ok "Whisper model already exists: $WHISPER_MODEL"
  else
    info "Downloading Whisper model: $WHISPER_MODEL..."
    mkdir -p "$WHISPER_DIR"
    WHISPER_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$WHISPER_MODEL"
    if curl -L --fail --progress-bar -o "$WHISPER_PATH" "$WHISPER_URL" 2>/dev/null; then
      ok "Whisper model downloaded: $WHISPER_MODEL"
    else
      rm -f "$WHISPER_PATH"
      warn "Whisper model download failed — download manually from:"
      warn "  $WHISPER_URL"
      warn "  Save to: $WHISPER_PATH"
    fi
  fi

  # ── Download Piper voice ───────────────────────────────────────────
  PIPER_VOICE="${DOTFILES_PIPER_VOICE:-en_US-lessac-high}"
  PIPER_DIR="$HOME/.local/share/piper-voices"
  # Parse voice name: en_US-lessac-high → en/en_US/lessac/high
  # Format: {locale}-{name}-{quality}
  #   locale = en_US, name = lessac, quality = high
  # URL path = {lang}/{locale}/{name}/{quality}
  #   lang is the language prefix before _ in locale (en_US → en)
  VOICE_LOCALE="$(echo "$PIPER_VOICE" | cut -d- -f1)"
  VOICE_NAME="$(echo "$PIPER_VOICE" | cut -d- -f2)"
  VOICE_QUALITY="$(echo "$PIPER_VOICE" | cut -d- -f3)"
  VOICE_LANG="$(echo "$VOICE_LOCALE" | cut -d_ -f1)"
  VOICE_URL_PATH="${VOICE_LANG}/${VOICE_LOCALE}/${VOICE_NAME}/${VOICE_QUALITY}"
  PIPER_ONNX="$PIPER_DIR/$PIPER_VOICE.onnx"
  PIPER_JSON="$PIPER_DIR/$PIPER_VOICE.onnx.json"
  PIPER_BASE_URL="https://huggingface.co/rhasspy/piper-voices/resolve/main"

  PIPER_DOWNLOAD_NEEDED=0
  if [[ ! -f "$PIPER_ONNX" ]]; then
    PIPER_DOWNLOAD_NEEDED=1
  fi
  if [[ ! -f "$PIPER_JSON" ]]; then
    PIPER_DOWNLOAD_NEEDED=1
  fi

  if [[ "$PIPER_DOWNLOAD_NEEDED" -eq 0 ]]; then
    ok "Piper voice already exists: $PIPER_VOICE"
  else
    info "Downloading Piper voice: $PIPER_VOICE..."
    mkdir -p "$PIPER_DIR"
    DL_FAILED=0

    if [[ ! -f "$PIPER_ONNX" ]]; then
      ONNX_URL="$PIPER_BASE_URL/$VOICE_URL_PATH/$PIPER_VOICE.onnx"
      if curl -L --fail --progress-bar -o "$PIPER_ONNX" "$ONNX_URL" 2>/dev/null; then
        ok "Piper voice .onnx downloaded"
      else
        rm -f "$PIPER_ONNX"
        warn "Piper voice .onnx download failed — download manually from:"
        warn "  $ONNX_URL"
        warn "  Save to: $PIPER_ONNX"
        DL_FAILED=1
      fi
    fi

    if [[ ! -f "$PIPER_JSON" ]]; then
      JSON_URL="$PIPER_BASE_URL/$VOICE_URL_PATH/$PIPER_VOICE.onnx.json"
      if curl -L --fail --progress-bar -o "$PIPER_JSON" "$JSON_URL" 2>/dev/null; then
        ok "Piper voice .onnx.json downloaded"
      else
        rm -f "$PIPER_JSON"
        warn "Piper voice .onnx.json download failed — download manually from:"
        warn "  $JSON_URL"
        warn "  Save to: $PIPER_JSON"
        DL_FAILED=1
      fi
    fi

    if [[ "$DL_FAILED" -eq 0 ]]; then
      ok "Piper voice setup complete: $PIPER_VOICE"
    fi
  fi
fi

# ---------------------------------------------------------------------------
# 9. ACP (Agent Client Protocol) adapters
#    Each ACP agent requires its own prior login/auth state:
#      - OpenCode: configured via this script
#      - Gemini: GEMINI_API_KEY env var or `gemini auth`
#      - Claude Code: `claude /login` (claude-code-acp wraps claude)
#      - Codex: OpenAI auth (codex-acp wraps codex)
#      - Junie: JetBrains IDE login
#      - Copilot: GitHub auth (`copilot auth` or GITHUB_TOKEN)
# ---------------------------------------------------------------------------
if command -v copilot >/dev/null 2>&1; then
  ok "GitHub Copilot CLI already installed"
else
  info "Installing GitHub Copilot CLI..."
  if brew install copilot-cli 2>/dev/null; then
    ok "GitHub Copilot CLI installed"
  else
    warn "Failed to install GitHub Copilot CLI (public preview — may require manual install)"
  fi
fi

# Claude Code ACP adapter (agentclientprotocol adapter)
if npm list -g @agentclientprotocol/claude-agent-acp &>/dev/null 2>&1; then
  ok "@agentclientprotocol/claude-agent-acp already installed"
else
  info "Installing @agentclientprotocol/claude-agent-acp..."
  if npm install -g @agentclientprotocol/claude-agent-acp@latest 2>/dev/null; then
    ok "@agentclientprotocol/claude-agent-acp installed"
  else
    warn "Failed to install @agentclientprotocol/claude-agent-acp"
  fi
fi

# Codex ACP adapter (agentclientprotocol adapter)
if npm list -g @agentclientprotocol/codex-acp &>/dev/null 2>&1; then
  ok "@agentclientprotocol/codex-acp already installed"
else
  info "Installing @agentclientprotocol/codex-acp..."
  if npm install -g @agentclientprotocol/codex-acp@latest 2>/dev/null; then
    ok "@agentclientprotocol/codex-acp installed"
  else
    warn "Failed to install @agentclientprotocol/codex-acp"
  fi
fi

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
  4. Voice setup:        configure-opencode-voice.py --preset <tier>
  5. Voice deps:         DOTFILES_RUN_VOICE_SETUP=1 install-opencode.sh
      → Installs whisper-cpp, sox, piper-tts, and downloads models
  6. ACP adapters:       install-opencode.sh
      → ACP adapters installed (Copilot CLI, claude-code-acp, codex-acp).
        After install, run: make brewfile-sync
        (captures the new npm/brew entries into Brewfiles for reproducibility)
  7. Skills:             configure-skills.py
      → Distributes skills from configs/skills/ to all agent skill directories
  8. lazyskills:         lazyskills find --json \"query\"
      → Search and discover skills from the registry
  9. Tokenscope:         Run /tokenscope in OpenCode UI to verify
      → Token usage and cost analysis for OpenCode sessions
  10. ACP auth:          Run \`copilot auth\` for Copilot ACP agent
      → Required before using copilot-acp agent
  11. Restart OpenCode:  Restart OpenCode after install for all plugins to load

Install script complete!"
