# Multiplexer Integration

> Deep reference for tmux/zellij side-by-side editing with OpenCode.

oh-my-opencode-slim supports tmux and zellij multiplexer integration for side-by-side editing with an AI agent pane.

---

## Configuration

The multiplexer config is in `configs/opencode/oh-my-opencode-slim.json`:

```json
"multiplexer": {
  "type": "auto",
  "layout": "main-vertical",
  "main_pane_size": 60
}
```

| Field | Values | Default | Description |
|-------|--------|---------|-------------|
| `type` | `"auto"`, `"tmux"`, `"zellij"`, `"none"` | `"auto"` | Auto-detects installed multiplexer |
| `layout` | `"main-vertical"`, `"main-horizontal"`, `"tiled"`, `"even-horizontal"`, `"even-vertical"` | `"main-vertical"` | Pane layout style |
| `main_pane_size` | `20`–`80` (tmux percentage) | `60` | Size of the main pane |
| `zellij_pane_mode` | `"agent-tab"`, `"current-tab"` | `"agent-tab"` | Zellij pane placement mode |

---

## Launching

Multiplexer mode requires starting OpenCode with the `--port` flag. The `opencode` shell function (defined in `dot_dotfiles/shell/aliases.sh`) handles this automatically:

- **Inside tmux/zellij:** auto-injects `--port` for the TUI (default command only — subcommands like `models`, `serve`, `run` pass through without `--port`)
- **Outside multiplexer:** runs `opencode` normally, no `--port` flag

```bash
# Inside tmux or zellij — port flag is automatic
opencode

# Pass additional flags — port is still auto-injected
opencode --chat
opencode --preset local

# Subcommands pass through unchanged (no --port injection)
opencode models
opencode serve

# Override port via env var
OPENCODE_PORT=5000 opencode

# Outside tmux/zellij — normal opencode, no port flag
opencode
```

---

## Prerequisites

- **tmux**: Already in `Brewfile` (installed via `make deploy`)
- **zellij**: Already in `Brewfile` (installed via `make deploy`)
- **Shell wrapper**: `opencode` function in `dot_dotfiles/shell/aliases.sh`

---

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENCODE_PORT` | Random (49152–65535) | Port for multiplexer communication between OpenCode and the editor pane |

`OPENCODE_PORT` defaults to a random high port via `jot -r 1 49152 65535`, avoiding conflicts with other opencode instances (ACP servers, headless serve, etc.). Set it explicitly in `~/.env` or on the command line if you need a fixed port.

---

## Common Tasks

| Task | Command |
|------|---------|
| Launch OpenCode (auto-multiplexer) | `opencode` (inside tmux/zellij: port is automatic) |
| Launch with specific port | `OPENCODE_PORT=5000 opencode` |
| Change multiplexer layout | Edit `configs/opencode/oh-my-opencode-slim.json`, then `scripts/configure-opencode.py` |
| Switch multiplexer type | Edit `type` in slim.json (`auto`/`tmux`/`zellij`/`none`), then `scripts/configure-opencode.py` |
