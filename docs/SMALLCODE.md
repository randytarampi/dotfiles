# SmallCode Integration

> SmallCode terminal-native coding agent: tier mapping, escalation, config generation, and environment gating.
> See [AGENTS.md](../AGENTS.md) for the lean agent guidance index.

## SmallCode Integration

[SmallCode](https://github.com/Doorman11991/smallcode) is a terminal-native coding agent for small local models (8B–35B). It provides budgeted context, forgiving tool-call parsing, search/replace patching, persistent memory, and adaptive cloud escalation.

### Key Properties

- **Local-first**: Designed for small local models with guardrails (context budgets, loop detection, validation)
- **Tier-aware**: Maps OpenCode tiers to SmallCode model routing (FAST/DEFAULT/MEDIUM/STRONG)
- **Adaptive escalation**: After 3+ calls, if failure rate >0.3 → MEDIUM, >0.6 → STRONG
- **MCP support**: Runs as MCP server via `smallcode --mcp`; config at `~/.config/smallcode/mcp.json`
- **Skills**: Discovers from `~/.smallcode/skills/`, `~/.config/smallcode/skills/`, `.smallcode/skills/`, `.agents/skills/`, `.claude/skills/`

### Files

| File | Purpose |
|------|---------|
| `scripts/configure-smallcode.py` | Tier-aware config generator (env + TOML + MCP) |
| `scripts/install-smallcode.sh` | Install CLI + plugins (gated on `DOTFILES_RUN_SMALLCODE_SETUP`) |
| `configs/mcp/smallcode.json` | MCP template (command + args) |
| `.chezmoiscripts/run_onchange_10-install-smallcode.sh.tmpl` | Chezmoi install (gated on `DOTFILES_RUN_SMALLCODE_SETUP`) |
| `.chezmoiscripts/run_onchange_18-configure-smallcode.sh.tmpl` | Chezmoi config (gated on `DOTFILES_RUN_SMALLCODE_SETUP`) |
| `dot_dotfiles/shell/.env.example` | SMALLCODE_* env vars |
| `dot_dotfiles/shell/aliases.sh` | `smallcode()` passthrough wrapper |

### Tier Mapping

SmallCode derives its cloud routing tiers from `configs/opencode/oh-my-opencode-slim.json` using this role mapping:

| SmallCode Slot | OpenCode Role |
|---------------|-------------|
| DEFAULT | orchestrator |
| FAST | librarian |
| MEDIUM | fixer |
| STRONG | oracle |

Escalation uses the STRONG/oracle model and inherits the oracle model's provider for provider/key selection.

Local tiers still resolve `_local:*` placeholders via `configure-opencode-tier.py`'s `resolve_roles_from_list()`. Local models always use `SMALLCODE_PROVIDER=openai` with `SMALLCODE_BASE_URL` from `get_ollama_local_base_url()` (Ollama's OpenAI-compatible endpoint, respects `OLLAMA_LOCAL_HOST`/`OLLAMA_LOCAL_PORT` env vars).

For Anthropic models (used by `anthropic`, `pro-plus-anthropic` presets), SmallCode routes through the Meridian proxy when `is_meridian_configured()` is true, using `get_meridian_base_url()` which respects `MERIDIAN_HOST`/`MERIDIAN_PORT` env vars.

`resolve_local_models()` prefers `resolve_roles_from_list()` directly (no side effects). The `orchestrate_tier_switch()` fallback exists but writes OpenCode config as a side effect — it is used only when the direct approach fails.

### Escalation

Escalation config is written to `config.toml` only when cloud API keys are available:

- `ANTHROPIC_API_KEY` set → escalate via the oracle/STRONG model using Anthropic
- `OPENAI_API_KEY` set → escalate via the oracle/STRONG model using OpenAI

When escalation provider is `openai` or `ollama-cloud`, the TOML only writes `provider` and `model` — SmallCode uses its own defaults or `SMALLCODE_BASE_URL_*` env vars for the base URL. For `meridian`, the full `baseUrl` is written from `get_meridian_base_url()`.

### Context Budget

`SMALLCODE_CONTEXT_BUDGET=67` — aligned with OpenCode's DCP compaction threshold (67%), not SmallCode's default (70%).

### Config Generation

`scripts/configure-smallcode.py` writes:

1. `~/.config/smallcode/.env` — SmallCode env vars (model, base URL, provider, context budget)
2. `~/.config/smallcode/config.toml` — Config (model, escalation)
3. `~/.config/smallcode/mcp.json` — MCP client config (empty placeholder, written directly)

### Environment Gating

SmallCode setup is gated on `DOTFILES_RUN_SMALLCODE_SETUP=1` (default: 0 in `.env.example`, but must be explicitly set in `~/.env`).

### Shell Wrapper

The `smallcode()` function in `aliases.sh` is a simple passthrough — no multiplexer detection needed (unlike `opencode`).

### Chezmoi Phase

Phase 10 (`run_onchange_10-install-smallcode.sh.tmpl`) installs the SmallCode CLI. It:

1. Checks `DOTFILES_RUN_SMALLCODE_SETUP` gate
2. Delegates to `scripts/install-smallcode.sh` (npm/bun global install + verification)

Phase 18 (`run_onchange_18-configure-smallcode.sh.tmpl`) runs after OpenCode config (phase 16). It:

1. Checks `DOTFILES_RUN_SMALLCODE_SETUP` gate
2. Checks `smallcode` CLI availability
3. Auto-detects tier from API keys (mirrors OpenCode detection logic)
4. Forwards `DOTFILES_SMALLCODE_TIER`, `DOTFILES_LOCAL_FALLBACK_PRESET`, `DOTFILES_LOCAL_FALLBACK_PLACEHOLDERS`, `DOTFILES_LOCAL_FALLBACK_ROLES`
5. Calls `scripts/configure-smallcode.py --preset <tier>`

### Common Tasks

| Task | Command |
|------|---------|
| Configure SmallCode | `scripts/configure-smallcode.py --preset <tier>` |
| Configure without local models | `scripts/configure-smallcode.py --preset pro --no-local-fallbacks` |
| Configure SmallCode with custom Ollama URL | `scripts/configure-smallcode.py --preset local --ollama-base-url http://custom:11434/v1` |
| Install SmallCode CLI | `scripts/install-smallcode.sh` |
| Regenerate SmallCode MCP config | `scripts/configure-smallcode.py --preset <tier>` (MCP written directly) |

> [!WARNING]
> `--ollama-base-url` overrides ALL base URLs uniformly. For cloud presets where different models use different providers, this may produce incorrect routing. Use primarily with local tiers.
