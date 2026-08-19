# DCP Context Compaction

> Deep reference for context compaction thresholds and OpenCode config paths.

---

## Compaction Thresholds

`~/.config/opencode/dcp.jsonc` uses percentage-based thresholds. Soft nudges
begin at **33%** context usage. Strong compression nudges begin at **67%**.
DCP relies on the model choosing to use the compress tool.

DCP prefers `dcp.jsonc` over `dcp.json`, so only one of these files should
exist.

No per-model config needed — the plugin reads context windows from provider configs.

---

## OpenCode Config Paths

Cross-platform OpenCode configuration paths:
- macOS/Linux: `~/.config/opencode/opencode.json`
- Windows: `%USERPROFILE%\.config\opencode\opencode.json`
- Cache: `~/.cache/opencode/` (macOS/Linux), `%USERPROFILE%\.cache\opencode` (Windows)
- Data: `~/.local/share/opencode/` (macOS/Linux), `%USERPROFILE%\.local\share\opencode` (Windows)

Both the CLI and desktop app read from `~/.config/opencode/` — no symlinks needed.

---

## TUI Panel (`/dcp`)

Since v3.1.13, DCP ships a TUI panel entrypoint (`./tui`) alongside its server entrypoint (`./server`). The panel provides:
- Context window visualization and stats
- Manual-mode controls (`manualMode.enabled`, `manualMode.automaticStrategies`)
- `/dcp-compress [focus]` for prompt-triggered manual compression

**Loading the panel requires DCP in `tui.json`** (in addition to `opencode.json` for core compression):

```json
{
  "$schema": "https://opencode.ai/tui.json",
  "plugin": [
    "@tarquinen/opencode-dcp@latest",
    ["@renjfk/opencode-voice", { "...": "..." }]
  ]
}
```

No options tuple is needed for the DCP entry — the panel reads thresholds and state from `~/.config/opencode/dcp.jsonc`. The DCP entry is written by `scripts/configure-opencode-dcp.py`, which defensively merges into `tui.json` (creating the file if missing, touching only the DCP entry). Other TUI plugins (e.g. voice) are preserved.

> [!NOTE]
> `tui.json` is a shared file — each TUI plugin has its own `configure-opencode-*.py` that defensively merges only its own entry. See [VOICE.md](VOICE.md) for the voice plugin's equivalent.
