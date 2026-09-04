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
| `~/.pi/agent/agents/` | Custom subagents (delegation uses pi-subagents built-ins) |

Set `PI_CODING_AGENT_DIR` to override the directory. Pi maps DEFAULT to
orchestrator, FAST to librarian, MEDIUM to fixer, and STRONG to oracle.
Local models use the shared tier resolver; providers include Ollama, Ollama
Cloud, Meridian, OpenAI, Google, OpenRouter, and OpenCode Zen. Provider models
are emitted only when their API key is configured; missing providers are warned
about and skipped. For cloud tiers, the fallback ACP agent's full
`~/.pi-local` configuration is materialized using
`DOTFILES_LOCAL_FALLBACK_PRESET` (default: `local`) passed as `--preset`
so the fallback is fully local. `local-*` tiers skip that duplicate
configuration, omit `@pi--local` from the ACP registry, and clean stale
fallback files on deploy.

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

## Project-scoped configuration

Run `scripts/configure-project.py --steps pi` or set `DOTFILES_PROJECT_PI=1` to
run `configure-pi.py --mode project` in the project root. It writes
`.pi/agent/settings.json`, `.pi/agent/models.json`, and `.pi/agent/auth.json`
relative to the project; `auth.json` contains environment-variable references
only, not secrets. Global plugin configuration seeding is skipped in project
mode. These files are machine-specific, so projects may want `.pi/` in
`.gitignore`. Regeneration re-syncs the tier snapshot, which can drift after a
global tier switch until the project configuration is regenerated. See
[docs/ORCHESTRATION.md](ORCHESTRATION.md) for the project configuration flow.

## Usage patterns

- Use Pi as a lightweight, scriptable terminal agent when a full OpenCode
  session is unnecessary.
- Switch between OpenAI, Anthropic, Ollama, and Meridian providers through
  Pi's generated `models.json`.
- Delegate parallel work to the pi-subagents built-ins (scout, researcher,
  worker, reviewer, oracle, delegate); models are pinned from the preset.

## Model resolution

`configure-pi.py` derives every model from the preset — no hardcoded model
names. The default model is the preset's `orchestrator` role; the
`FAST`/`MEDIUM`/`STRONG` slots resolve through `ROLE_TO_BUILTIN` → the shared
`resolve_roles_from_list()` tier resolver.

For `local*` presets, `moe_codegen_reuse=True` is passed to the resolver. When
the code-gen model is MoE and vision-capable (e.g. `ornith-1.5:35b`), it serves
code-gen, lightweight, and vision roles — so `researcher`, `scout`, and
`reviewer` built-ins all use the single loaded MoE model. See
[docs/TIERS.md](TIERS.md) for the full reuse rules.

`--local-fallback-placeholder`, `--local-fallback-role`, and
`--local-fallback-preset` override resolved categories after classification;
user overrides always win over MoE-reuse defaults. The resolver logs
`Classified local models:` on every run, matching `configure-opencode-tier.py`
and `generate-jetbrains-profiles.py`. All three consume the shared tier registry
via `scripts/lib/tier_registry.py`.

`--skip mcps` is accepted for CLI parity (Pi does not configure MCPs; the flag
is a no-op with an info log).
- Enable `pi-web-access` for web-backed research and use the Plannotator Pi
  extension for plan review.
- From OpenCode, delegate with `@pi` or the local Ollama `@pi--local` ACP
  entry.

## Local model timeouts

Pi has three timeout layers that affect local Ollama inference:

| Timeout | Location | Purpose | Default | Our setting |
|---|---|---|---|---:|
| `httpIdleTimeoutMs` | `~/.pi/agent/settings.json` | Undici body/headers idle timeout + fallback provider timeout | `300000` (5 min) | `900000` (15 min) |
| `retry.provider.timeoutMs` | `~/.pi/agent/settings.json` | Pi → provider HTTP request | SDK default (600000 / 10 min) | not set (SDK default) |
| `timeoutMs` (ACP) | `configs/opencode/acp-agents.json` | OpenCode → ACP agent session | `300000` (5 min) | `900000` (15 min, `--local` only) |

### Why `httpIdleTimeoutMs` matters

Pi's `DEFAULT_HTTP_IDLE_TIMEOUT_MS = 300_000` (5 min) governs both the Undici
transport's `bodyTimeout`/`headersTimeout` and the fallback value for
`retry.provider.timeoutMs` when that key is absent. The 5-minute default is too
short for cold 27B Ollama model loads, causing `"Error: Request timed out."`
followed by `"Aborted after 3 retry attempts"`.

We override `httpIdleTimeoutMs` to `900000` (15 min) and leave
`retry.provider.timeoutMs` unset so the SDK defaults apply (OpenAI SDK:
600000ms / 10 min, Anthropic SDK: 600000ms / 10 min). The HTTP idle timeout
must be ≥ the provider timeout, so 15 min > 10 min is correct.

### `OLLAMA_KEEP_ALIVE` coordination

`OLLAMA_KEEP_ALIVE` (default 5m, recommended 20m for interactive coding) controls
how long Ollama keeps a model loaded **after** the last request completes. It does
not affect in-progress requests — an active request keeps the model loaded
regardless of keep-alive.

The timeouts serve different purposes:

- **HTTP idle timeout** must exceed the worst-case inference time (cold model load
  + token generation). 15 minutes covers cold 27B loads + long agentic generations.
- **Keep-alive** must exceed the expected idle gap between agent requests. Too
  short and the model unloads between turns, adding cold-load latency to every
  request.

Rule of thumb:

```
httpIdleTimeoutMs > cold-load time + expected generation time + safety margin
OLLAMA_KEEP_ALIVE > expected idle interval between requests
```

### Other agents

Pi is unusual in exposing `httpIdleTimeoutMs` and `retry.provider.timeoutMs`.
Claude Code, Codex CLI, Gemini CLI, and Junie do not expose equivalent settings
for local model calls. For those agents, the ACP session `timeoutMs` (bumped to
`900000` for `--local` variants) is the only tunable timeout.

### Recommended values

| Workload | `httpIdleTimeoutMs` | Keep-alive |
|---|---:|---:|
| Small GPU model (≤8B) | 5 min (default) | 10–20 min |
| Large GPU model (27B+) | 15 min | 20–30 min |
| CPU inference | 30–60 min | 30–60 min |
| Always-on workstation | 30–60 min | `-1` (indefinite) |
