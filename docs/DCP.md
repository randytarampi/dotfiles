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

OpenCode v2 also uses `~/.config/opencode/cli.json` for terminal-only plugins.
The legacy global `tui.json` is imported non-destructively when `cli.json` is
absent and v2 starts; the migration-aware DCP writer performs that import when
it needs to write the file.

Both the CLI and desktop app read from `~/.config/opencode/` — no symlinks needed.

---

## TUI Panel (`/dcp`)

Since v3.1.13, DCP ships a TUI panel entrypoint (`./tui`) alongside its server entrypoint (`./server`). The panel provides:
- Context window visualization and stats
- Manual-mode controls (`manualMode.enabled`, `manualMode.automaticStrategies`)
- `/dcp-compress [focus]` for prompt-triggered manual compression

**Loading the panel requires DCP in `cli.json`** (in addition to `opencode.json` for core compression):

```json
{
  "$schema": "https://opencode.ai/v2/cli.json",
  "plugins": [
    "@tarquinen/opencode-dcp@3.2.0"
  ]
}
```

No options tuple is needed for the DCP entry — the panel reads thresholds and state from `~/.config/opencode/dcp.jsonc`. The entry is written by `scripts/configure-opencode-dcp.py`, which reads existing `cli.json` or imports legacy `tui.json`, removes the unsupported voice entry, preserves unrelated settings and plugins, then writes native v2 `cli.json`.

> [!NOTE]
> `cli.json` is the shared terminal-plugin file. Its writer is migration-aware so
> legacy `tui.json` settings are not discarded during the v2 transition.
