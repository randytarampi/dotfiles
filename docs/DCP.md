# DCP Context Compaction

> Deep reference for context compaction thresholds and OpenCode config paths.

---

## Compaction Thresholds

`~/.config/opencode/dcp.json` uses percentage-based thresholds:
- Compress at **67%** of context window
- Leave at least **20%** filled

No per-model config needed — the plugin reads context windows from provider configs.

---

## OpenCode Config Paths

Cross-platform OpenCode configuration paths:
- macOS/Linux: `~/.config/opencode/opencode.json`
- Windows: `%USERPROFILE%\.config\opencode\opencode.json`
- Cache: `~/.cache/opencode/` (macOS/Linux), `%USERPROFILE%\.cache\opencode` (Windows)
- Data: `~/.local/share/opencode/` (macOS/Linux), `%USERPROFILE%\.local\share\opencode` (Windows)

Both the CLI and desktop app read from `~/.config/opencode/` — no symlinks needed.
