# AI Agent Guidance

> **This is the authoritative agent guidance document for this dotfiles repo.**
> For human-facing quick start, commands, and package management, see `README.md`.
> For deep reference material, see the `docs/` directory.
> For historical migration notes, see `docs/CHEZMOI_MIGRATION_PLAN.md` (agents should not need to consult it).

---

## Repo Structure

Key directories and files that agents interact with:

```
~/Development/dotfiles/           # chezmoi source directory
├── .chezmoidata/
│   └── categories.yaml           # Brewfile + wingetfile category toggles
├── .chezmoiignore                # Ignore patterns (excludes scripts/, configs/, macOS-only on non-macOS)
├── .chezmoiscripts/              # 18 ordered run scripts (defaults → packages → config → smallcode)
│   ├── # Phase 1: System defaults (01–07)
│   ├── # Phase 2: Package installation (08–11)
│   └── # Phase 3: Tool configuration (12–17)
├── configs/
│   ├── agents/home-agents.md     # Source of truth for home-level agent guidance
│   ├── junie/model-groups.json   # Junie model profile definitions
│   ├── mcp/                      # MCP server configs
│   │   ├── betterstack.json
│   │   ├── codegraph.json         # CodeGraph — local-first semantic code index (stdio MCP)
│   │   ├── github.json
│   │   ├── global-mcps.json       # Tool→template registry
│   │   ├── idea.json              # JetBrains MCP — SSE transport (stdio via IJ_MCP_TRANSPORT=stdio)
│   │   ├── mongodb.json
│   │   ├── notion.json
│   │   ├── sentry.json            # SENTRY_ACCESS_TOKEN via env, not CLI args
│   │   └── shortcut.json
│   ├── iterm2/Default.json
│   ├── mozart-router/mozart.json
│   └── opencode/
│       ├── oh-my-opencode-slim.json  # Presets, council, fallbacks, tier overrides
│       ├── vibeguard.config.json      # VibeGuard sensitive-string redaction config
│       ├── anthropic-models.json     # Relocated
│       ├── role-to-local-category.json # New
│       ├── openai-models.json        # New
│       └── ollama-cloud-models.json  # New
├── docs/                          # Deep reference docs (linked from AGENTS.md)
│   ├── TIERS.md                  # Tier definitions, model classification, fallback, variants
│   ├── MOZART.md                 # Mozart router, gateways, Ollama routing, provider overrides
│   ├── SMALLCODE.md              # SmallCode integration, tier mapping, escalation
│   ├── VOICE.md                  # Voice plugin, STT/TTS, tier-aware config
│   ├── JUNIE.md                  # Junie model groups ↔ Oh My OpenCode sync
│   ├── MULTIPLEXER.md            # tmux/zellij side-by-side editing with OpenCode
│   ├── DCP.md                    # Context compaction thresholds and config paths
│   ├── ADDING.md                 # Adding new tiers and MCP servers
│   └── INSTALL.md                # Full installation & run instructions
├── AGENTS.md                    # THIS FILE — agent guidance (source of truth)
├── dot_config/
│   ├── opencode/dcp.json          # DCP context compaction thresholds
│   └── plannotator/config.json.tmpl
├── scripts/                      # Utility scripts + lib/
│   ├── lib/                       # Shared helpers
│   │   ├── common.sh              # Standardized logging & ERR trap stack traces
│   │   ├── env.sh                 # load_env(), alias_github_token() for shell bootstrap
│   │   ├── tier_detect.sh         # Shared tier auto-detection for chezmoi scripts
│   │   ├── tier_args.sh           # Shared local fallback arg forwarding for chezmoi scripts
│   │   ├── logger.py              # Central Python logging module
│   │   ├── env.py                 # load_env() and token aliases in Python
│   │   ├── constants.py           # Shared constants (BASE_URLS, etc.)
│   │   ├── file_utils.py          # Shared file utilities (backup_file, write_text_file)
│   │   ├── ai_mcps.py             # Filter template globs in Python
│   │   ├── ai_dirs.py             # Python-ported platform-independent directory setups
│   │   ├── ai_models.py           # Model prefix mappings, temperatures, strip_provider_prefix
│   │   ├── opencode_config.py     # OpenCode tier/preset helpers (get_available_tiers, build_tier_args)
│   │   ├── idea.py                # Resolve IntelliJ app paths, java, and MCP classpaths in Python
│   │   └── discover_models.py     # Local Ollama discovery to JSON in Python
│   ├── configure-mcp-all.py       # Generate MCP configs for all AI tools
│   ├── configure-jetbrains-ai.py  # JetBrains AI: models, dirs, symlinks, MCP
│   ├── configure-opencode-project.py # Write project-specific OpenCode config overrides
│   ├── configure-agent-guidance.py # Distribute home-level guidance to all agent files
│   ├── configure-mozart-router.py # Configure Mozart AI router
│   ├── configure-ai.py            # Resolve paths/secrets for AI tool .env files
│   ├── configure-jetbrains-workspace.py # Configure AI dirs in JB workspace modules
│   ├── verify-brewfile-completeness.py # Verify Brewfile completeness
│   ├── detect-ij-mcp.py           # Detect JetBrains MCP server paths (SSE default)
│   ├── configure-mcp-tool.py      # Generate MCP config for a single tool
│   ├── configure-meridian.py      # Add Meridian proxy to OpenCode config
│   ├── configure-opencode.py      # Write OpenCode config (local ollama default)
│   ├── configure-opencode-tier.py # Switch active preset tier (source of truth)
│   ├── configure-opencode-voice.py # Write voice plugin config (tui.json, tier-aware)
│   ├── generate-jetbrains-profiles.py # Generate model profiles JSON files
│   ├── get-tools.py               # Get MCP tool registry keys
│   ├── install-opencode.sh        # Install OpenCode plugins and tools (incl. voice)
│   ├── install-nvm-lts.sh         # Reinstall all LTS node versions
│   ├── update-nvm-globals.sh     # Update npm globals across all nvm versions
│   └── meridian-launch.sh         # Launch wrapper for meridian (Keychain-aware)
└── private_dot_ssh/config        # SSH config
```

---

## Installation

> See [docs/INSTALL.md](docs/INSTALL.md) for full step-by-step instructions, or `README.md` for the human-facing quick start.

Quick reference:
```bash
command -v chezmoi >/dev/null 2>&1 || brew install chezmoi
chezmoi init --source ~/Development/dotfiles
make diff    # Preview changes
make deploy  # Apply all dotfiles
```

---

## Scripting Conventions

### Boilerplate

Every script must follow this pattern:

```bash
#!/usr/bin/env bash
set -euo pipefail

# Resolve real script location (works when invoked via symlink from ~/bin/)
_SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
LIB_DIR="$SCRIPT_DIR/lib"

source "$LIB_DIR/common.sh"
# source "$LIB_DIR/env.sh"        # If env loading needed
# source "$LIB_DIR/tier_detect.sh" # For tier auto-detection (detect_tier)
# source "$LIB_DIR/tier_args.sh"   # For local fallback arg forwarding (build_tier_extra_args)
# Use scripts/lib/discover_models.py for Ollama discovery in Python scripts
# Use scripts/lib/ai_models.py for model prefix mappings, temperatures, and strip_provider_prefix
# Use scripts/lib/constants.py for shared constants (BASE_URLS, get_meridian_base_url, get_ollama_local_base_url, get_provider_base_url, is_meridian_configured, PROVIDER_BASE_URL_ENVS)
# Use scripts/lib/file_utils.py for backup_file() and write_text_file()
# Use scripts/lib/opencode_config.py for get_available_tiers() and build_tier_args()
```

### Naming

- `configure-*.py/sh` — tool/environment configuration scripts
- `install-*.sh` — installation scripts (idempotent)
- `verify-*.py` — verification/check scripts
- `detect-*.py` — detection/inspection scripts
- `generate-*.py` — code/model generation scripts
- `get-*.py` — query/inspection scripts

### Structure

All scripts follow: parse args → load env → gate check → main logic → ok/die

### Environment Gating

Scripts that should be toggleable check `DOTFILES_RUN_*` env vars:

```bash
if [[ "${DOTFILES_RUN_WHATEVER:-0}" != "1" ]]; then
  info "DOTFILES_RUN_WHATEVER='${DOTFILES_RUN_WHATEVER:-0}' — skipping"
  exit 0
fi
```

Default is 0 (skip). Set to 1 in `~/.env` to enable.

### Logging & Output Standardization

To ensure clean, prefix-continuous, and readable logs:
- Avoid noisy, verbose line dividers like `============================================` or unnecessary trailing blank `print()` calls.
- Summary blocks or multi-line messages must be aggregated and logged in a single call to preserve prefix consistency and prevent output formatting breaks.
- In Python, compile summary lines into an array and log them with `\n`:
  ```python
  summary_lines = [
      "OpenCode configured!",
      "",
      f"Config written to: {config_dir_path}",
      f"  • opencode.json (providers, MCP servers, plugins)"
  ]
  logger.info("\n".join(summary_lines))
  ```
- In Bash, log multi-line text blocks in a single `info` or `ok` call:
  ```bash
  info "OpenCode tools installed!

  Next steps:
    1. Write config:       configure-opencode.py

  Install script complete!"
  ```

### Style Rules

- **Indent:** 2-space (not tabs), enforced by `.editorconfig` + `shfmt`
- **Lint:** `shellcheck`, `shfmt`, `pre-commit` (local/offline `make lint`), `black`, JSON/YAML/Large files validation via `Makefile`
- **Homebrew-agnostic paths:** Always use `$(brew --prefix)` — never hardcode `/usr/local` or `/opt/homebrew`
- **Network resilience:** Never `set -e` on network calls; use `|| warn` pattern
- **Idempotency:** All `run_once_*` scripts must be safe to re-run
- **Env vars:** `DOTFILES_` prefix for dotfiles-system toggles, `DOTFILES_RUN_*` for script gates

---

## Model Tiers

Eleven tiers defined in `scripts/configure-opencode-tier.py` (source of truth). Switch with: `scripts/configure-opencode-tier.py <tier>`

| Tier | Providers | Best For |
|------|-----------|----------|
| **pro** | Ollama Cloud (glm-5.1 orchestrator, nemotron-3-ultra council) | Daily coding, budget mode |
| **pro-plus** | Ollama Cloud + OpenAI (`gpt-5.5`) | General development |
| **pro-plus-anthropic** | Anthropic + Ollama Cloud + OpenAI | Heavy orchestration |
| **plus** | OpenAI only (`gpt-5.5`, `gpt-5.4-mini`) | OpenAI-first workflow |
| **plus-anthropic** | OpenAI + Anthropic (no Ollama Cloud) | OpenAI + Anthropic hybrid |
| **anthropic** | Anthropic only | Anthropic-first workflow |
| **local-pro** | Local Ollama (all 4 categories) | Power users with diverse local models |
| **local** | Local Ollama (reasoning + code-gen + lightweight + vision) | Balanced offline/air-gapped |
| **local-mini** | Local Ollama (code-gen + lightweight + vision) | Minimal model diversity |
| **local-nano** | Local Ollama (single code-gen model + vision) | Single-model systems |
| **local-solo** | Local Ollama (single omnicapable model) | Maximum per-request quality, single-model simplicity |

Default preset: auto-detected from available API keys during `run_once_14-configure-opencode.sh.tmpl`. Detection order: both OpenAI + Anthropic keys → pro-plus-anthropic, Anthropic only → anthropic, OpenAI only → plus, no keys but Ollama → local, nothing → pro. Local-pro, local-mini, local-nano, and local-solo are manual-only (set via `DOTFILES_OPENCODE_TIER`).

> [!NOTE]
> For detailed tier definitions, per-tier role/variant tables, local model classification rules, fallback chains, and variant policy, see [docs/TIERS.md](docs/TIERS.md).

---

## CodeGraph Integration

[CodeGraph](https://github.com/colbymchenry/codegraph) is a local-first semantic code index + MCP server.

- **Zero-config**: No config file, no API keys, fully local
- **MCP server**: `codegraph serve --mcp` (stdio transport)
- **Parent-walk**: CodeGraph automatically walks up from CWD to find `.codegraph/` in parent directories. A project in `~/Development/dotfiles` will use `~/Development/.codegraph/` if no local index exists.
- **`projectPath` parameter**: All CodeGraph MCP tools accept a `projectPath` parameter to query a specific indexed project. Use this only when parent-walk won't find the right index (sibling/unrelated directories). The value is the directory containing `.codegraph/`, not the `.codegraph/` directory itself. `projectPath` overrides parent-walk.
- **Fallback rule**: Always try CodeGraph tools first. If they return empty results, fall back to grep/glob/read. Do not retry CodeGraph for the same query.
- **Installation**: If codegraph MCP tools are unavailable (server failed to start), install: `scripts/install-opencode.sh` (or `npm i -g @colbymchenry/codegraph`)
- **⚠️ Bare `codegraph` triggers the interactive installer** — use `codegraph status`, `codegraph init`, `codegraph install`, etc.

| Situation | Action |
|-----------|--------|
| Working in an indexed project | Nothing — parent-walk finds it |
| Working in a subdirectory of an indexed parent | Nothing — parent-walk finds it |
| Need to query a sibling project's index | Pass `projectPath` to that project's root |
| No `.codegraph/` anywhere in ancestor chain | Pass `projectPath` to a known indexed directory, or fall back to grep/glob |

**EMFILE troubleshooting**: If you see `EMFILE: too many open files, watch` errors in `~/.codegraph/daemon.log` on large indexes, either:
- Increase system limits: `sudo sysctl -w kern.maxfiles=65536 kern.maxfilesperproc=65536`
- Or disable file watching: use `codegraph serve --mcp --no-watch` in your MCP config (rebuild index manually with `codegraph init` after changes)

Available steps for `configure-opencode-project.py --steps`: `opencode` (always), `tier`, `codegraph` (opt-in), `mcps`. Default: `opencode,tier`. Run `codegraph init` manually in any directory you want to index.

---

## Secrets Management

`~/.env` is the single source of truth for all secrets. Format: `KEY='VALUE'`. Templates use `{{ env "VAR" }}` syntax.

Prefer Makefile targets (`make diff`, `make dry-run`, `make deploy`) because they load `~/.env` in the same shell process as chezmoi. Use `make env-check` to report drift and `make env-sync` to append newly documented keys.

Key template files: `dot_gitconfig.tmpl` (GIT_AUTHOR_*, GPG_SIGNING_KEY, GITHUB_USER, GH_TOKEN), `private_dot_npmrc.tmpl` (NPM_TOKEN, GH_TOKEN), `private_dot_aws/credentials.tmpl` (AWS_*), `private_dot_vuescanrc.tmpl` (VUESCAN_*), `private_dot_gnupg/gpg.conf.tmpl` (GPG_SIGNING_KEY).

---

## AI Agent Guidance Files

- `AGENTS.md` (this file) is repo-level guidance for agents working on the dotfiles repo itself. It is in `.chezmoiignore` and is NOT deployed to `~/AGENTS.md`.
- `configs/agents/home-agents.md` is the source of truth for home-level agent guidance. The script `configure-agent-guidance.py` distributes it to `~/AGENTS.md` and all 6 agent locations: `~/.claude/CLAUDE.md`, `~/.gemini/GEMINI.md`, `~/.codex/AGENTS.md`, `~/.config/opencode/AGENTS.md`, `~/.cursor/AGENTS.md`, and `~/.ai/AGENTS.md` (resolving `~/.junie` symlink).
- Deep reference material lives in `docs/` (linked throughout this file).

When editing home-level agent guidance, edit `configs/agents/home-agents.md` first, then run `scripts/configure-agent-guidance.py` to distribute. For repo-level guidance (this file), edit `AGENTS.md` directly. All cross-references should link to `docs/` files.

---

## Common Tasks

| Task | Command |
|------|---------|
| Switch AI tier | `scripts/configure-opencode-tier.py <tier>` |
| Switch tier without local Ollama | `scripts/configure-opencode-tier.py --no-local-fallbacks <tier>` |
| Switch tier with local fallback role override | `scripts/configure-opencode-tier.py --local-fallback-role observer=ollama/qwen3.5:9b-mlx <tier>` |
| Switch tier with local fallback preset | `scripts/configure-opencode-tier.py --local-fallback-preset local-pro pro-plus` |
| Regenerate all MCP configs | `scripts/configure-mcp-all.py` |
| Regenerate single MCP config | `scripts/configure-mcp-tool.py <tool> <server>` |
| Regenerate OpenCode config | `scripts/configure-opencode.py` |
| Regenerate project config | `scripts/configure-opencode-project.py` |
| Configure voice plugin | `scripts/configure-opencode-voice.py --preset <tier>` |
| Configure SmallCode | `scripts/configure-smallcode.py --preset <tier>` |
| Configure Mozart router | `scripts/configure-mozart-router.py` |
| Add Meridian to OpenCode | `scripts/configure-meridian.py` |
| Configure JetBrains AI | `scripts/configure-jetbrains-ai.py --all` |
| Setup AI env files | `scripts/configure-ai.py` |
| Install OpenCode plugins | `scripts/install-opencode.sh` |
| Distribute agent guidance | `scripts/configure-agent-guidance.py` |
| Check env template drift | `make env-check` |
| Preview pending changes | `make diff` |
| Apply all dotfiles | `make deploy` |

> For more tasks (SmallCode, voice, multiplexer, Ollama routing, JetBrains), see the relevant docs: [SMALLCODE.md](docs/SMALLCODE.md), [VOICE.md](docs/VOICE.md), [MULTIPLEXER.md](docs/MULTIPLEXER.md), [MOZART.md](docs/MOZART.md), [JUNIE.md](docs/JUNIE.md).

---

## Reference Docs Index

| Doc | Content |
|-----|---------|
| [docs/TIERS.md](docs/TIERS.md) | Tier definitions, per-tier role/variant tables, local model classification, fallback chains, variant policy, Ollama Cloud models |
| [docs/MOZART.md](docs/MOZART.md) | Mozart router gateways, unified Ollama routing, provider overrides, JSON config convention |
| [docs/SMALLCODE.md](docs/SMALLCODE.md) | SmallCode integration, tier mapping, escalation, config generation, environment gating |
| [docs/VOICE.md](docs/VOICE.md) | Voice plugin, tier-aware STT/TTS, dependencies, model defaults, config locations |
| [docs/JUNIE.md](docs/JUNIE.md) | Junie model groups ↔ Oh My OpenCode sync, mapping rules, temperature overrides, deployment |
| [docs/MULTIPLEXER.md](docs/MULTIPLEXER.md) | tmux/zellij side-by-side editing, configuration, launching, prerequisites |
| [docs/DCP.md](docs/DCP.md) | Context compaction thresholds, OpenCode config paths |
| [docs/ADDING.md](docs/ADDING.md) | Adding a new tier, adding an MCP server |
| [docs/INSTALL.md](docs/INSTALL.md) | Full installation & run instructions (prerequisites, cloning, env seeding, chezmoi, verification) |

---

## Operational Philosophy

- **Local-repo trust:** No age/GPG encryption because the repo is local-only
- **Idempotent scripts:** Every `run_once_*` must be safe to re-run
- **Graceful degradation:** Network failures in chezmoi scripts warn, never abort
- **Single sources of truth:** `scripts/` for shell logic, `configs/` for JSON configs, `~/.env` for secrets, `AGENTS.md` for agent docs
- **Homebrew-agnostic paths:** Always `$(brew --prefix)` — never hardcode platform paths
- **2-space shell indent:** Enforced by `.editorconfig` + `shfmt`
- **Commits:** `feat/fix/refactor/chore/docs` with scopes: `dotfiles`, `brew`, `secrets`, `scripts`, `templates`, `infra`, `agents`
