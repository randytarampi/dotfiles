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

The LiteLLM-routed `poolside/laguna-s-2.1` OpenCode entry is conservatively
advertised with ceiling limits of 262,112 tokens of context and 32,768 tokens of
output, preserving any smaller positive limits already configured. This is a
routed policy based on observed gateway behaviour, not a claim about Poolside's
full native context window. At 67%, DCP's strong-compression nudge is about
175,615 tokens; with the output allowance, that remains below the observed
262,112-token gateway boundary. DCP nudges rely on the model choosing the compress
tool, so they do not guarantee automatic compression before the limit. Direct
Poolside entries retain their existing metadata.

## Compaction ownership

The generated `opencode.json` sets `"compaction": {"auto": false}`. Native
automatic compaction is therefore disabled so DCP is the sole automatic
compactor. Manual `/compact` remains available.

DCP's quiet-related keys are intentionally not set; this is a deliberate user
decision even though the documented keys exist. No experimental keys or DCP
namespace is added to `opencode.json`.

The previously reported LSP noise is closed as documentation-only: the Python
import-resolution messages are pre-existing editor artefacts, `py_compile` is
the ground truth, and no upstream suppression keys exist. `lsp` therefore
remains `true`.

The `startId not available` message is plugin/OpenCode lifecycle behaviour and
has no upstream configuration remedy.

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

**On OpenCode v2 the panel loads from the `opencode.json` plugins manifest** — TUI-capable plugins listed there auto-load in the CLI/TUI; there is no separate `tui.json` registration. The manifest entry (`configs/opencode/opencode-plugins.json`, installed by script 07) is `@tarquinen/opencode-dcp@latest`.

`scripts/configure-opencode-dcp.py` no longer registers DCP anywhere: it only removes stale DCP entries it previously managed in `tui.json`, and only after confirming that `opencode.json` actually registers DCP (the dcp.jsonc/dcp.json configuration file is deployed by chezmoi and is not touched by this writer). Voice (now in `cli.json`) and all other entries are preserved.

> [!NOTE]
> On v2 the only shared plugin files are `opencode.json` (server + TUI-capable plugins via the manifest) and `cli.json` (global TUI-client-only plugins); `tui.json` remains user-owned. See [VOICE.md](VOICE.md) for the voice plugin's `cli.json` entry.
