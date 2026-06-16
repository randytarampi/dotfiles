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
# 6. Install CodeGraph CLI (local semantic code index + MCP server)
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
# 7. Install opencode-voice plugin (voice input/output for TUI)
# ---------------------------------------------------------------------------
info "Installing @renjfk/opencode-voice..."
opencode plugin @renjfk/opencode-voice@latest --global
ok "opencode-voice plugin installed"

# ---------------------------------------------------------------------------
# 8. Install voice dependencies (whisper-cpp, sox, piper-tts) + models
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

Install script complete!"
