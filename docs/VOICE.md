# Voice Plugin (historical OpenCode integration)

> Historical reference for the removed OpenCode voice plugin and current substitutes.

## Removed in OpenCode v2

`@renjfk/opencode-voice` is V1-only and is not loaded by OpenCode v2. This is an
accepted loss; no redaction or compatibility mitigation is promised. The former
`configure-opencode-voice.py` writer is removed.

For voice input, use macOS dictation or Pi's STT support. In OpenCode, `/rename`
regains the `ctrl+r` key binding previously supplied by the voice workflow.
The historical tier and STT/TTS notes below are retained for reference only.

Historically, OpenCode voice support was provided by [`@renjfk/opencode-voice`](https://github.com/renjfk/opencode-voice), a TUI-only plugin that added voice input (STT) and output (TTS).

---

## Key Properties

- **TUI-only**: The plugin only hooks into the TUI, not the desktop app or VSCode extension
- **Historical configuration**: `tui.json` was separate from `opencode.json`; it was written by `configure-opencode-voice.py`
- **Tier-aware**: Voice LLM endpoint and STT backend are selected based on the active preset
- **Local-first**: Default uses local Ollama + whisper-cli; cloud STT is an upgrade when API keys are available

---

## Voice Config Generation

Historically, `scripts/configure-opencode-voice.py` wrote `~/.config/opencode/tui.json` with a tier-aware voice plugin config after tier switching. It is no longer run.

| Tier | Voice LLM | STT Backend |
|------|-----------|-------------|
| **local-pro** | Best local Ollama/oMLX model (auto-detected) | whisper-cli (local), oMLX audio STT when available |
| **local** | Best local Ollama/oMLX model (auto-detected) | whisper-cli (local), oMLX audio STT when available |
| **local-mini** | Best local Ollama/oMLX model (auto-detected) | whisper-cli (local), oMLX audio STT when available |
| **local-nano** | Best local Ollama/oMLX model (auto-detected) | whisper-cli (local), oMLX audio STT when available |
| **local-solo** | Best local Ollama/oMLX model (auto-detected) | whisper-cli (local), oMLX audio STT when available |
| **pro** | `gemma4:31b` via Ollama Cloud | whisper-cli (local), OpenAI STT if key available |
| **pro-plus** | `gemma4:31b` via Ollama Cloud | whisper-cli (local), OpenAI STT if key available |
| **pro-plus-anthropic** | `gemma4:31b` via Ollama Cloud | whisper-cli (local), OpenAI STT if key available |
| **plus** | `gpt-5.6-luna` via OpenAI | OpenAI STT |
| **plus-anthropic** | `gpt-5.6-luna` via OpenAI | OpenAI STT |
| **anthropic** | Meridian proxy or `claude-haiku-4-5` | whisper-cli (local), OpenAI STT if key available |
| **free** | OpenAI-compatible remote endpoint; no Ollama requirement | whisper-cli (local) |
| **omo-slim-openai** | `gpt-5.6-luna` via OpenAI | OpenAI STT |
| **omo-slim-thirty-dollars** | `gpt-5.6-luna` via OpenAI | OpenAI STT |
| **omo-slim-opencode-zen-free** | OpenCode Zen | whisper-cli (local), OpenAI STT if key available |

**Meridian detection**: `is_meridian_configured()` from `constants.py` controls Meridian routing for voice. If it returns true, the Anthropic tier uses Meridian as the voice LLM endpoint. Otherwise it falls back to direct Anthropic API.

**Cloud STT upgrade**: When `OPENAI_API_KEY` is available, non-OpenAI tiers add `sttEndpoint`/`sttModel`/`sttApiKeyEnv` pointing to OpenAI's `/v1/audio/transcriptions`. Tiers already using OpenAI for the LLM use OpenAI STT by default.

**Local Ollama model selection**: All `local-*` tiers reuse the shared tier registry (`scripts/lib/tier_registry.py`) model discovery — they pick the best local model for voice based on capability heuristics (preferring audio/vision-capable models). Cloud tiers respect `DOTFILES_USE_LOCAL_OLLAMA` (default: true) to control whether local models replace the cloud LLM endpoint.

When enabled and reachable, the shared local pool may select an oMLX LLM or
`audio_stt` model. This changes the voice normalization LLM endpoint and the
STT backend; TTS remains Piper, and
`DOTFILES_USE_LOCAL_OMLX=false` opts out of the oMLX STT path.

The `free` tier is explicitly excluded from Ollama Cloud fallback handling; it
does not require `OLLAMA_API_KEY` or a local Ollama daemon for voice.

---

## STT/TTS Dependencies

Voice requires local STT/TTS tooling regardless of tier:

| Component | Install | Purpose |
|-----------|---------|---------|
| `whisper.cpp` | `brew install whisper.cpp` | Local speech-to-text |
| `sox` | `brew install sox` | Audio format conversion (required by whisper-cli) |
| `piper-tts` | `uv tool install piper-tts` | Local text-to-speech |
| Whisper model | Download to `~/.local/share/whisper-cpp/` | STT model file |
| Piper voice | Download to `~/.local/share/piper-voices/` | TTS voice file |

Historically, these were installed by `run_onchange_07-install-opencode-plugins.sh.tmpl`
(gated on `DOTFILES_RUN_VOICE_SETUP=1`); OpenCode v2 no longer installs them.

---

## Model Defaults

| Component | Env Var | Default | Notes |
|-----------|---------|---------|-------|
| Whisper model | `DOTFILES_WHISPER_MODEL` | `ggml-large-v3-turbo.bin` | Best balance of accuracy (1.5 GiB) |
| Piper voice | `DOTFILES_PIPER_VOICE` | `en_US-lessac-high` | High-quality English voice |

Piper voice URL is constructed from components: `en_US-lessac-high` → `en/en_US/lessac/high/en_US-lessac-high.onnx`

---

## Voice Plugin Config Locations

| File | Purpose |
|------|---------|
| `~/.config/opencode/tui.json` | Historical v1 voice/TUI plugin config. OpenCode v2 uses `cli.json`; see [DCP.md](DCP.md). |
| `~/.local/share/whisper-cpp/` | Whisper model directory |
| `~/.local/share/piper-voices/` | Piper voice directory |
| `~/.local/bin/piper` | Piper TTS binary (installed by `uv tool install piper-tts`) |

---

## Environment Gating

The historical voice dependencies and `DOTFILES_RUN_VOICE_SETUP` gate applied to
the removed v1 plugin. OpenCode v2 does not run the voice config writer.

## Cross-tool voice support

| Tool | Voice support | Mechanism | Notes |
|---|---|---|---|
| opencode | Removed in v2 | macOS dictation; `/rename` restores `ctrl+r` | The V1-only plugin loss was accepted |
| pi | STT only | `@juicesharp/rpiv-voice` plugin | TTS not available; hallucination filter configurable |
| claude | STT only | built-in `/voice` dictation | No TTS; speech-to-text only |
| codex | None | — | No voice plugin or built-in support |
| copilot | None | — | No voice plugin or built-in support |
| gemini | None | — | No voice plugin or built-in support |
| cursor | None | — | No voice plugin or built-in support |
| cline | None | — | No voice plugin or built-in support |
| junie | None | — | No voice plugin or built-in support |
| cortex | None | — | No voice plugin or built-in support |
| agy (Antigravity) | None | — | No voice plugin or built-in support |

## Sharing voice infrastructure across tools

Voice plugins are tool-specific — each tool requires its own plugin and cannot
reuse another tool's voice runtime. However, the underlying services and models
can be shared:

### Whisper models (STT)

Pi uses a fixed sherpa-onnx Whisper model under `~/.pi/models/whisper-base/`.
The OpenCode voice plugin uses a separate whisper-cli/whisper-cpp model and
format. The two runtimes cannot practically share model files due to different
model formats and fixed paths.

### Piper TTS

Piper was OpenCode-only (via `@renjfk/opencode-voice`). No other tool
in this repo has Piper integration. The Piper voice model is configured via
`DOTFILES_PIPER_VOICE` and managed by the removed voice writer.

### i18n / locale

No tool in this repo has dedicated i18n plugin support beyond Pi's `@juicesharp/rpiv-i18n`
plugin. Locale behaviour for all other tools falls back to the standard `LANG`
and `LC_ALL` environment variables (see `dot_dotfiles/shell/.env.example`).
Note: `LANG`/`LC_ALL` provide OS locale input to processes; they do not guarantee
that each tool's UI is translated. Pi's i18n plugin only localizes participating
`rpiv-*` extension UI.

### Why voice can't be shared like guidance

Agent guidance (`AGENTS.md`) is plain markdown that any tool can read. Voice
plugins are binary/integrated runtimes — a Whisper process, audio capture, TTS
engine — that must run inside the host tool's process. There is no protocol for
cross-tool voice sharing, unlike MCP/ACP which are designed for inter-tool communication.
