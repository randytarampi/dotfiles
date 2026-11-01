# Agent Tool Capability Matrix


Optional inference: OpenCode, Pi, Junie and LiteLLM support explicitly
credentialed Groq, Mistral, Cloudflare Workers AI and NVIDIA NIM routes;
GitHub's OpenCode review workflow provides trusted single-model selection
with the existing Big Pickle default. This does not imply that other tools
have runtime 429 fallback. See [OPTIONAL_INFERENCE.md](OPTIONAL_INFERENCE.md).

This matrix records the supported integration surface for each agent. “Native”
means the tool consumes the configuration directly; “adapter” means an ACP
bridge or compatibility layer is used.

| Tool | Providers | MCP | ACP | Skills | Presets | Guidance | Meridian | Local Fallback | Telemetry | Voice | i18n |
|---|---|---|---|---|---|---|---|---|---|---|---|
| opencode | OpenAI, Anthropic, Ollama/oMLX/Ollama Cloud, OpenCode Zen, GitHub Copilot, Meridian | native OpenCode config; global registry template | native `opencode acp` | yes, `~/.config/opencode/skills` | yes, registry-derived tiers (17) | yes | yes | native tiers incl. `local-solo` | not collected | full (STT+TTS) | env-vars |
| codex | OpenAI, Ollama and oMLX via `config.toml` profiles | TOML adapter from global registry | `codex-acp` adapter | yes, `~/.codex/skills` | pool-driven local profile | yes | yes, through provider URL/profile | `@codex--local` | env var + config | none | env-vars |
| hermes | Inspected custom-provider schema; opt-in LiteLLM scaffold only | not assessed | not assessed | not assessed | not assessed | not assessed | not assessed | not assessed | not qualified | not assessed | not assessed |
| junie | JetBrains/cloud, OpenAI, Meridian, Ollama, oMLX | native shared `~/.ai/mcp/mcp.json` | `junie --acp true` | yes, `~/.ai/skills` (`~/.junie` symlink) | model groups | yes; no verified redaction hooks (UNKNOWN) | no direct Meridian integration | `@junie--local` | IDE setting | none | env-vars |
| pi | OpenAI, Anthropic, Ollama/oMLX/Ollama Cloud, Meridian | adapter-owned `~/.pi/agent/mcp-adapter.json` via `pi-mcp-adapter` | `pi-acp` | yes, `~/.pi/agent/skills` | subagents | yes, `~/.pi/agent/AGENTS.md` | yes, as a `models.json` provider | `@pi--local` | env var + config | stt-only | plugin; VibeGuard baseline redaction, optional UI extension |
| cortex | Snowflake Cortex only | native Cortex MCP (`cortex mcp add`) | native `cortex acp serve` | yes, `~/.snowflake/cortex/skills` | no | yes, `~/.snowflake/cortex/AGENTS.md` | no | N/A | no local opt-out | none | env-vars |
| claude | Anthropic and compatible `ANTHROPIC_BASE_URL` endpoints | native Claude configuration | `claude-agent-acp` adapter | yes, `~/.claude/skills` | settings | yes | via environment/provider URL | `@claude--local` | env var | stt-only | env-vars |
| Claude Desktop | Anthropic or explicitly applied third-party gateway (Chat/Cowork/Code) | not assessed | not assessed | not assessed | not assessed | not assessed | not assessed | not assessed | not assessed | not assessed | not assessed |
| Claude CLI | Anthropic-compatible endpoints; standalone LiteLLM CLI wrapper | native Claude configuration | `claude-agent-acp` adapter (separate from standalone CLI wrapper) | yes, `~/.claude/skills` | settings | yes | via environment/provider URL | `@claude--local` | env var | stt-only | env-vars |
| copilot | GitHub Copilot / environment-configured providers; opt-in LiteLLM CLI launcher (live request UNKNOWN) | native/config varies by CLI | Copilot ACP adapter | yes, `~/.copilot/skills` | no | yes | no | UNSUPPORTED | no local opt-out | none | env-vars |
| gemini | Google Gemini / environment-configured providers | native/config varies by CLI | `gemini --acp` adapter | yes, `~/.gemini/skills` | no | yes | no | `@gemini--local` (experimental) | env var | none | env-vars |
| cursor | Cursor-configured providers | native `~/.cursor/mcp.json` | `cursor-agent acp` | yes, `~/.cursor/skills` | no | yes | no | N/A | no local opt-out | none | env-vars |
| cline | Cline-configured providers | native/config varies by extension | `cline --acp` | yes, shared skill distribution | no | yes | no | N/A | env var (Cline OTel) | none | env-vars |
| agy | Antigravity-configured providers | native `~/.gemini/config/mcp_config.json` | native `agy-acp` bridge | yes, `~/.gemini/antigravity-cli/skills` | no | yes | no | N/A | no local opt-out | none | env-vars |

Claude Desktop gateway support is an explicit export scaffold only:
`scripts/export-claude-desktop-gateway.py` prints a secret-free profile to
stdout. Owner installation/application, credential delivery, and live
compatibility remain UNKNOWN. Desktop Chat, Cowork, and Code acceptance are
tracked separately; this is not the Claude CLI environment-variable route.

## Tier families and service lanes

- The registry contains 17 tiers: three local resolution profiles
  (`local-quiet`, `local`, `local-loud`) plus standalone `local-solo`; see
  [TIERS.md](TIERS.md) for the canonical table. The verifier derives the count
  from the registry.
- OpenCode also supports `local-solo`, selected with
  `scripts/configure-opencode-tier.py --preset`.
  Junie and Pi receive the selected family through their generators.
- OpenAI Flex/Fast lane aliases are gated by
  `DOTFILES_USE_OPENAI_SERVICE_LANES` (default `0`). Clients receive aliases
  only when the gate is enabled and the genuine direct OpenAI API-key route is
  active. See [LiteLLM Proxy](LITELLM.md#openai-service-lanes-flexfast).
- Pi's local effective context window is capped at the minimum of native
  context, when known, and client cap; unknown native context has a 32768
  fallback floor. The default client cap is 131072. See [Pi](PI.md#effective-context-window).

## Local-service fleet disposition

| Tool | Local chat service use | Audio service use | Unsupported disposition |
|---|---|---|---|
| OpenCode | Ollama and gated oMLX via shared tier selection | Voice plugin: whisper-cli/OpenAI STT and optional discovered oMLX STT; Piper TTS | — |
| Junie | Ollama/oMLX via generated model profiles | Unsupported | No Junie voice integration |
| Pi | Ollama/oMLX providers and tier selection | sherpa-onnx STT | TTS unsupported |
| Codex | Ollama/oMLX through generated provider profiles | Unsupported | No voice integration |
| Open WebUI | Registry-managed local engine connections | Audio settings managed by the reconciler; task configuration is not managed (planned) | — |
| Other listed agents | No generated local service consumer unless their row explicitly says otherwise | Unsupported | See each tool's local-fallback and voice cells above |

| AMD local service | Disposition |
|---|---|
| Ollama via Vulkan | Planned; serving and discovery verification only. Not benchmarked. |

## Configuration notes

- The opt-in VibeGuard scanner pilot is exposed as `make vibeguard-audit`; its
  wrapper pins the scanner and rejects mutating flags. It is separate from
  request-time redaction and does not replace it.

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
- Xcode's LiteLLM integration is credential-only: an installed, identity-checked
  app and all three opt-in gates permit a private `xcode.key`. IDE consumption,
  effective authentication and routed inference remain UNKNOWN. Keys are
  per client, not per operating system; see [LITELLM.md](LITELLM.md).
- `scripts/configure-hermes.py` adds `providers.dotfiles-litellm` to
  `$HERMES_HOME/config.yaml` only when `DOTFILES_RUN_HERMES_SETUP=1` and
  `DOTFILES_USE_LITELLM_PROXY=1`; choose it explicitly with
  `hermes --provider dotfiles-litellm --model <served-alias>`. It does not
  change Hermes' default provider. On the inspected Hermes 0.22.4 installation,
  custom-provider `key_env` may be superseded by saved provider-pool credentials;
  credential delivery and live transport are not qualified.
