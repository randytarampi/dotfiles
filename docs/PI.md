# Pi Coding Agent

Pi is earendil-works' terminal coding-agent harness: multi-provider,
extensible, and configured from the shared OpenCode tier registry.

## Files

| File | Purpose |
|---|---|
| `~/.pi/agent/settings.json` | Defaults, packages, skills, and subagents |
| `~/.pi/agent/models.json` | Provider and model definitions |
| `~/.pi/agent/auth.json` | Environment-variable API-key references |
| `~/.pi/agent/mcp.json` | Generated MCP servers |
| `~/.pi/agent/agents/` | Generated subagent roles |

Set `PI_CODING_AGENT_DIR` to override the directory. Pi maps DEFAULT to
orchestrator, FAST to librarian, MEDIUM to fixer, and STRONG to oracle.
Local models use the shared tier resolver; providers include Ollama, Ollama
Cloud, Meridian, and OpenAI.

`pi-mcp-adapter` reads the generated MCP file and `pi-acp` exposes Pi to
OpenCode's ACP registry. Packages include `pi-web-access`, `pi-subagents`,
`@plannotator/pi-extension`, and `pi-skills`. Skills are linked into
`~/.pi/agent/skills` by the shared reconciler.

```sh
pi
DOTFILES_RUN_PI_SETUP=1 make configure
make verify
```

Installation runs in phase 10 and configuration in phase 18, after OpenCode.
Both are opt-in through `DOTFILES_RUN_PI_SETUP=1`.
