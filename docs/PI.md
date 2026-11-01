# Pi Coding Agent


Pi supports the optional Groq, Mistral, Cloudflare Workers AI and NVIDIA
NIM direct provider pool when enabled and credentialed, or advertised
LiteLLM aliases in proxy mode. Missing credentials omit the optional model
families. See [OPTIONAL_INFERENCE.md](OPTIONAL_INFERENCE.md).

Pi is earendil-works' terminal coding-agent harness: multi-provider,
extensible, and configured from the shared OpenCode tier registry.

## Files

| File | Purpose |
|---|---|
| `~/.pi/agent/settings.json` | Defaults, packages, skills, and subagents |
| `~/.pi/agent/models.json` | Provider and model definitions |
| `~/.pi/agent/auth.json` | Environment-variable API-key references |
| `~/.pi/agent/mcp-adapter.json` | Adapter-owned MCP servers, settings, imports, and overrides |
| `~/.pi/agent/agents/` | Custom subagents (delegation uses pi-subagents built-ins) |

Set `PI_CODING_AGENT_DIR` to override the directory. Pi maps DEFAULT to
orchestrator, FAST to librarian, MEDIUM to fixer, and STRONG to oracle.
Local models use the shared tier resolver; providers include Ollama, oMLX, Ollama
Cloud, Meridian, OpenAI, Google, OpenRouter, and OpenCode Zen. Cloud provider
models are emitted only when their API key is configured; missing dynamic
providers are warned about and skipped (Ollama Cloud is always emitted, with a
runtime `$OLLAMA_API_KEY` reference). Keyless-capable local engines (oMLX) are
different: pi treats a provider without an `apiKey` as unauthenticated and
drops it from model resolution, so `configure-pi.py` emits a literal
placeholder apiKey (`"omlx"`) for engines marked `api_key_optional` when their
key env var is unset — oMLX no-key mode accepts any bearer token. oMLX itself
stays loopback-only in no-key mode; Caddy owns external exposure. When the key
env var is set, the provider block references it (`$OMLX_API_KEY`).

Set `DOTFILES_USE_LITELLM_PROXY=1` to route through the LiteLLM gateway. This is
a gateway-only switch: `DOTFILES_RUN_LITELLM_SETUP=1`, Pi's client key file, and
the required exact gateway aliases must be available; missing prerequisites are
errors, with no direct-provider fallback. See [LiteLLM Proxy](LITELLM.md) for
setup and client-key requirements. Google models disable Pi's `store`
compatibility field; this setting is Google-specific.

Two runtime notes:

- Pi does not load `~/.env` itself. The referenced variable must be exported
  into pi's runtime environment (for example by shell initialization) for the
  provider to authenticate; otherwise the provider is dropped.
- The apiKey is fixed at generation time. Switching between keyed and keyless
  oMLX modes requires rerunning `configure-pi.py` (i.e. `make deploy` or
  `DOTFILES_RUN_PI_SETUP=1 make configure`); a generated literal placeholder
  does not later become a `$OMLX_API_KEY` reference on its own. For cloud tiers, the fallback ACP agent's full
`~/.pi-local` configuration is materialized using
`DOTFILES_LOCAL_FALLBACK_PRESET` (default: `local`) passed as `--preset`
so the fallback is fully local. `local-*` tiers skip that duplicate
configuration, omit `@pi--local` from the ACP registry, and clean stale
fallback files on deploy.

The repository generates and manages `~/.pi/agent/mcp-adapter.json` as the
adapter-owned MCP file. `configs/mcp/global-mcps.json` registers Pi with that
`mcp_path`; `configure-mcps.py` reconciles every registered tool by default,
and `configure-all.sh` invokes that reconciliation during MCP configuration.

Pi 0.87.1's startup guidance and adapter contract confirm that the adapter
reads `mcp-adapter.json` for its own settings, imports, and overrides, and no
longer reads Pi's `mcp.json`.
The migration is complete: when MCP setup is enabled, the writer migrates an
old `mcp.json` into `mcp-adapter.json`, merges both files with adapter entries
winning duplicate server names, appends old-only servers, and removes the old
file only after the content is accounted for. Pi's project `.pi/mcp.json`
remains built-in Pi configuration; the installed adapter package provides no
documented project-level adapter-file equivalent.

Packages include `pi-web-access`, `pi-subagents`, `@plannotator/pi-extension`,
the repo's configured `rpiv-*` extensions, and `@xzzpig/pi-vibeguard`. Skills are linked into
`~/.pi/agent/skills` by the shared reconciler.

### VibeGuard

Pi installs the upstream extension using `pi install npm:@xzzpig/pi-vibeguard`.
Its README describes pre-provider redaction as “Replaces configured sensitive
strings with placeholders before requests are sent to the LLM provider,” then
restoration in model output and before tool execution. The provider is intended
not to see plaintext secrets; runtime verification is reserved for the
machine-phase.

The managed config is `~/.pi/agent/vibeguard.config.json`, copied from
`configs/pi/vibeguard.config.json` by `configure-pi.py`. It contains audited
regex and built-in shapes, not raw secrets; `patterns.keywords` stays empty to
avoid duplicating secret values outside `~/.env`. The extension documents the
`/vibeguard:*` TUI command family.

VibeGuard's `__VG_` placeholder family aligns with OpenCode's VibeGuard plugin,
but this does not establish interchangeability with
`@taehwandev/vibeguard`'s separate `rulesPath` format.

### Skill collision status

The repo-local `dotfiles-verify` skill uses valid folded YAML frontmatter and
keeps its description below Pi's 1,024-character limit. `iamhumans` is sourced
from `hoainho/iamhumans` in `configs/skills/skills.core.json`, so its skill
file is external and is not edited here. Plannotator skills are likewise
external (or preinstalled), and the canvas skill is outside this repository;
those descriptions are therefore limitations rather than repo changes.

### Package decisions

`pi-edit-session-in-place` is the only optional Pi package approved for this
configuration. The rewind hook is not enabled. The following packages are
explicitly declined: `pi-side-chat`, `pi-intercom`, `pi-messenger`,
`pi-interactive-shell`, `pi-rewind-hook`, `pi-custom-compaction`, and
`pi-model-switch`.

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

`--category-model`, `--role-model`, and
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

### Effective context window

For local models, Pi advertises the minimum of the native context window (when
known) and the client cap. When native context is unknown, the fallback window
is 32768. The default client cap is 131072 (also the Ollama client cap when
`OLLAMA_CONTEXT_LENGTH` is unset or invalid). Model `maxTokens` is derived as
`max(4096, min(32768, window - 8192))`; it is not a blanket fixed value.
Tier switching also passes the selected local profile family through to Pi.

## Managed preference defaults

Generate a detached global saved profile with
`configure-pi.py --preset anthropic --profiles-only`. This offline mode reads
the shared registry and writes only an unselected profile and its private
provenance record. It does not inspect or change active settings, models, auth
or packages. `--dry-run` writes nothing. Local presets with unresolved model
placeholders are refused rather than emitted as incomplete assignments.
Only preset selection, global mode and `--dry-run` apply in this mode.
Explicit discovery, fallback, skip and model-override flags are usage errors
(exit 2), as is `--no-backup`: exclusive creation never replaces a profile or
creates a backup. Dry-run validates existing private leaf directories without
creating directories or changing permissions.

The profile file is created before its provenance manifest. If manifest
creation fails, the command reports a refused/partial creation and leaves the
profile in place at mode `0600`; later plans refuse that unowned artifact and
never adopt or delete it automatically. Do not rerun expecting automatic
recovery. Inspect the orphan and any related manifest paths, then make a
separate owner-controlled backup and audit the contents. Recovery is manual:
manually relocate only verified orphaned artifacts and their related provenance
to the private backup location, then rerun the same preset. The CLI accepts
registry presets, not arbitrary output names. If ownership is uncertain, stop
and preserve the files. Do not chmod or migrate existing directories
automatically: new directories request `0700`, so a permissive umask cannot
broaden their permissions; a more restrictive umask may cause safe refusal.
Pre-existing profile/provenance leaf directories with unsafe ownership or
permissions are refused unchanged. Ancestor traversal still assumes trusted,
owner-controlled parents; this is not concurrent-path protection.

Manual collisions and changed generated profiles are preserved; updates require
a future coordinated write boundary. Select a reviewed profile explicitly with
`/subagents-load-profile <name>` and inspect `/subagents-models`. The installed
loader replaces agent overrides wholesale, so a manual selection is a
destructive-category action, like OpenCode's `/preset` switch. The #103/#108
guards cover generation, detached-file ownership and collision protection of
the generated profiles only — they do not wrap or intercept the installed
extension's manual loader. Before selecting manually, preserve custom
assignments: privately snapshot/backup the relevant project and global subagent
settings, then read the loaded state back with `/subagents-models` (or
`/subagents-check-profile`) and compare. Project settings may shadow the
global selection; the operator owns that backup-and-readback discipline. The
owner has accepted this manual workflow in place of an automated coordinated
loader (ruling dated 2026-10-26), so preservation of custom settings is an
accepted manual discipline here, not a proven automatic guarantee. This mode
does not provide automatic loading or qualify strict-free execution.

The shared `free` presets are composed dynamically from fresh catalogue and
route-evidence inputs during generation. Insufficient or stale provenance
refuses selection instead of falling back silently. Generated evidence is not
request-time billing or entitlement enforcement, and this documentation does
not claim live availability was verified. The manual profile loader does not
itself refresh or validate this evidence.

`configure-pi.py` codifies defaults for these `~/.pi/agent/settings.json` keys:

| Key | Codified default |
|---|---|
| `theme` | `light/dark` |
| `tuiMode` | `fullscreen` |
| `markdown.mermaid` | `final` |
| `followUpMode` | `all` |
| `steeringMode` | `all` |
| `terminal.showTerminalProgress` | `true` |
| `showHardwareCursor` | `true` |

Precedence on regeneration: env `PI_THEME` (when non-empty) > the theme
previously written in `settings.json` > the codified default. All other
codified keys: a value you set in the Pi TUI wins over the codified default
(so `make deploy` never reverts TUI edits), and fresh installs are seeded
with the codified defaults. `lastChangelogVersion` is pi's own bookkeeping —
carried over when present, never written by the repo.

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
