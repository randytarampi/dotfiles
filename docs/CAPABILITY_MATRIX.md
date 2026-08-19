# Agent Tool Capability Matrix

This matrix records the supported integration surface for each agent. “Native”
means the tool consumes the configuration directly; “adapter” means an ACP
bridge or compatibility layer is used.

| Tool | Providers | MCP | ACP | Skills | Presets | Guidance | Meridian | Local Fallback |
|---|---|---|---|---|---|---|---|---|
| opencode | OpenAI, Anthropic, Ollama/Ollama Cloud, Meridian | native OpenCode config; global registry template | native `opencode acp` | yes, `~/.config/opencode/skills` | yes, 11 tiers | yes | yes | native tiers |
| codex | OpenAI and Ollama via `config.toml` profiles | TOML adapter from global registry | `codex-acp` adapter | yes, `~/.codex/skills` | profiles | yes | yes, through provider URL/profile | `@codex--local` |
| junie | JetBrains/cloud, OpenAI, Meridian, Ollama | native shared `~/.ai/mcp/mcp.json` | `junie --acp true` | yes, `~/.ai/skills` (`~/.junie` symlink) | model groups | yes | no direct Meridian integration | `@junie--local` |
| pi | OpenAI, Anthropic, Ollama/Ollama Cloud, Meridian | `pi-mcp-adapter`, generated Pi config | `pi-acp` | yes, `~/.pi/agent/skills` | subagents | yes, `~/.pi/agent/AGENTS.md` | yes, as a `models.json` provider | `@pi--local` |
| cortex | Snowflake Cortex only | native Cortex MCP (`cortex mcp add`) | native `cortex acp serve` | yes, `~/.snowflake/cortex/skills` | no | yes, `~/.snowflake/cortex/AGENTS.md` | no | N/A |
| claude | Anthropic and compatible `ANTHROPIC_BASE_URL` endpoints | native Claude configuration | `claude-agent-acp` adapter | yes, `~/.claude/skills` | settings | yes | via environment/provider URL | `@claude--local` |
| copilot | GitHub Copilot / environment-configured providers | native/config varies by CLI | Copilot ACP adapter | yes, `~/.copilot/skills` | no | yes | no | UNSUPPORTED |
| gemini | Google Gemini / environment-configured providers | native/config varies by CLI | `gemini --acp` adapter | yes, `~/.gemini/skills` | no | yes | no | `@gemini--local` (experimental) |
| cursor | Cursor-configured providers | native `~/.cursor/mcp.json` | `agent acp` | yes, `~/.cursor/skills` | no | yes | no | N/A |
| cline | Cline-configured providers | native/config varies by extension | `cline --acp` | yes, shared skill distribution | no | yes | no | N/A |
| agy | Antigravity-configured providers | native `~/.gemini/config/mcp_config.json` | native `agy-acp` bridge | yes, `~/.gemini/antigravity-cli/skills` | no | yes | no | N/A |

## Configuration notes

- Providers use each tool's native environment/config format. The OpenCode tier
  registry is reused by Pi, Junie, and local fallback generation where useful.
- `configs/mcp/global-mcps.json` contains templates for the eleven registry
  targets. The Pi template is present in each target; Cortex MCP remains
  deliberately native and is not merged into the global registry.
- `scripts/configure-acp-agents.py` detects commands with `shutil.which`. Local
  entries are generated only for tools with a supported local fallback.
- `scripts/configure-skills.py` reconciles the canonical store into all target
  directories, including Pi, Cortex, Antigravity, and Junie's resolved `~/.ai`.
- Meridian is an OpenCode plugin and provider endpoint. Pi consumes it through
  its generated `models.json`; tools marked “no” have no direct Meridian wiring.
  Mozart routes provider traffic and is not an additional per-tool ACP target.
