# AI Agent Guidance

> **This is the authoritative agent guidance document for this dotfiles repo.**
> For human-facing quick start, commands, and package management, see `README.md`.
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
│   ├── # Phase 3: Tool configuration (12–17)
├── configs/
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
├── AGENTS.md                    # THIS FILE — agent guidance (source of truth)
├── dot_config/
│   ├── opencode/dcp.json          # DCP context compaction thresholds
│   └── plannotator/config.json.tmpl
├── CLAUDE.md                     # Applied to ~/CLAUDE.md; keep aligned with AGENTS.md
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

## Initial Installation & Run Instructions

AI agents should adhere to the following sequence for deploying and verifying the repository across different environments:

### Prerequisites
- **macOS / Linux:** Homebrew (`brew`)
- **Windows:** PowerShell 7 (`pwsh`), Windows Package Manager (`winget`)

### Step 1: Repository Cloning
Clone the repository to your local development workspace (standard target is `~/Development/dotfiles` or `$HOME\Development\dotfiles`):
```bash
mkdir -p ~/Development
git clone https://github.com/<username>/dotfiles.git ~/Development/dotfiles
cd ~/Development/dotfiles
```

### Step 2: Local Environment Seeding (`.env`)
The single source of truth for secrets and toggles is `~/.env` (or `$HOME\.env` / `%USERPROFILE%\.env` on Windows):
1. Copy the canonical template from the repository:
   - **macOS / Linux:**
     ```bash
     cp dot_dotfiles/shell/.env.example ~/.env
     ```
   - **Windows (PowerShell):**
     ```powershell
     copy dot_dotfiles\shell\.env.example $HOME\.env
     ```
2. Populate the required secrets and configure active toggles. To run the automated package install during templating, set:
   ```env
   DOTFILES_RUN_INSTALL_PACKAGES=1
   ```

### Step 3: Makefile + chezmoi Orchestration
Initialize chezmoi, then use Makefile targets so `~/.env` is loaded in the same shell process as each chezmoi command:
- **macOS / Linux:**
  ```bash
  command -v chezmoi >/dev/null 2>&1 || brew install chezmoi
  chezmoi init --source ~/Development/dotfiles
  make diff
  make deploy
  ```
- **Windows (PowerShell 7):**
  ```powershell
  if (-not (Get-Command chezmoi -ErrorAction SilentlyContinue)) {
      winget install twpayne.chezmoi
  }
  chezmoi init --source "$HOME\Development\dotfiles"
  make deploy
  ```

### Step 4: Verification & Local Linting (Optional)
To verify repository and script health offline:
- **macOS / Linux:**
  ```bash
  # Install standard dev dependencies (black, shellcheck, shfmt, pre-commit)
  brew bundle --file Brewfile.dev

  # Set up local hooks
  pre-commit install

  # Run verification
  make test
  # or
  pre-commit run --all-files
  ```
- **Windows (PowerShell 7 / Git Bash):**
  ```powershell
  # Install dev tools via winget (automatic via DOTFILES_RUN_INSTALL_PACKAGES=1 on chezmoi apply)
  # Or install manually:
  winget install GnuWin32.Make OpenJS.NodeJS Python.Python.3.12 psf.black koalaman.shellcheck mvdan.shfmt

  # Set up local hooks
  pip install pre-commit
  pre-commit install

  # Run verification (native PowerShell or Git Bash / WSL for Unix utility compatibility)
  make test
  # or
  pre-commit run --all-files
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

## Model Selection Strategy

### Preset Switching

Use `/preset <name>` to switch models at runtime. **Runtime-safe fields only:**
- `model`, `temperature`, `variant`, `options`

Changing `prompt`, `skills`, `mcps`, or `displayName` requires an OpenCode restart. Define presets properly in `oh-my-opencode-slim.json` for persistent configuration.

### Tier Definitions

Eleven tiers defined in `scripts/configure-opencode-tier.py` (source of truth):

| Tier | Providers | Best For |
|------|-----------|----------|
| **pro** | Ollama Cloud (glm-5.1 orchestrator, nemotron-3-ultra council) | Daily coding, budget mode |
| **pro-plus** | Ollama Cloud + OpenAI (`gpt-5.5`) | General development |
| **pro-plus-anthropic** | Anthropic + Ollama Cloud + OpenAI | Heavy orchestration |
| **plus** | OpenAI only (`gpt-5.5`, `gpt-5.4-mini`) | OpenAI-first workflow |
| **plus-anthropic** | OpenAI + Anthropic (no Ollama Cloud) | OpenAI + Anthropic hybrid |
| **anthropic** | Anthropic only | Anthropic-first workflow |
| **local-pro** | Local Ollama (all 4 categories: reasoning, code-gen, lightweight, vision) | Power users with diverse local models |
| **local** | Local Ollama (reasoning + code-gen + lightweight + vision) | Balanced offline/air-gapped |
| **local-mini** | Local Ollama (code-gen + lightweight + vision) | Minimal model diversity |
| **local-nano** | Local Ollama (single code-gen model + vision) | Single-model systems |
| **local-solo** | Local Ollama (single omnicapable model) | Maximum per-request quality, single-model simplicity |

> [!NOTE]
> When both `OLLAMA_API_KEY` and `ANTHROPIC_API_KEY` are set (but not `OPENAI_API_KEY`), auto-detection returns `pro-plus-anthropic`. The tier name implies OpenAI is also present, but the preset works correctly without it — Ollama Cloud handles orchestrator and Anthropic handles oracle.

Cloud presets (pro, pro-plus, pro-plus-anthropic) use Ollama Cloud models including `nemotron-3-ultra`, `minimax-m3`, `glm-5.1`, `kimi-k2.6`, `kimi-k2.7-code`, `deepseek-v4-pro`, `deepseek-v4-flash`. The `plus` preset uses OpenAI models exclusively. The `plus-anthropic` preset uses OpenAI and Anthropic models without Ollama Cloud. The `anthropic` preset uses only Anthropic models. The `local-pro` preset uses all four `_local:<category>` placeholders resolved at runtime. The `local` preset uses reasoning + code-gen + lightweight + vision for a balanced 3-party council. The `local-mini` preset reduces to code-gen + lightweight + vision. The `local-nano` preset uses a single code-gen model for all roles (except vision) with a 2+1 council. The `local-solo` preset uses a single omnicapable model (completion+thinking+tools+vision) for all roles, with council diversity from variants rather than different models.

#### Anthropic Tier (`anthropic`)

Anthropic-only preset with no OpenAI or Ollama Cloud providers:

| Role | Model | Variant |
|------|-------|---------|
| orchestrator | `claude-opus-4-6` | — |
| oracle | `claude-opus-4-8` | xhigh |
| librarian | `claude-haiku-4-5` | low |
| explorer | `claude-haiku-4-5` | low |
| designer | `claude-sonnet-4-6` | medium |
| fixer | `claude-sonnet-4-6` | low |
| observer | `claude-haiku-4-5` | low |

Council agent is defined inside each preset's agent list; alpha `claude-opus-4-8`, beta `claude-sonnet-4-6`, gamma `claude-opus-4-6`. Empty fallback chains by default — local Ollama models are appended automatically unless `--no-local-fallbacks` is passed.

#### Plus-Anthropic Tier (`plus-anthropic`)

OpenAI + Anthropic preset with no Ollama Cloud providers:

| Role | Model | Variant |
|------|-------|---------|
| orchestrator | `openai/gpt-5.5` | — |
| oracle | `anthropic/claude-opus-4-8` | xhigh |
| librarian | `openai/gpt-5.4-nano` | low |
| explorer | `anthropic/claude-haiku-4-5` | low |
| designer | `anthropic/claude-sonnet-4-6` | medium |
| fixer | `openai/gpt-5.4-mini` | high |
| observer | `anthropic/claude-haiku-4-5` | low |

Council agent is defined inside each preset's agent list; alpha `claude-opus-4-8`, beta `gpt-5.5`, gamma `gpt-5.4`. Fallback chains mix OpenAI + Anthropic models per role — local Ollama models are appended automatically unless `--no-local-fallbacks` is passed.

#### Local-Pro Tier (`local-pro`)

Fully offline preset using all four `_local:<category>` placeholders:

| Role | Placeholder | Resolves to |
|------|------------|-------------|
| orchestrator | `_local:code-gen` | Best local code-gen model |
| oracle | `_local:reasoning` | Best local reasoning model |
| librarian | `_local:lightweight` | Best local lightweight model |
| explorer | `_local:lightweight` | Best local lightweight model |
| designer | `_local:code-gen` | Best local code-gen model |
| fixer | `_local:code-gen` | Best local code-gen model |
| observer | `_local:vision` | Best local vision-capable lightweight model |

Council: α `_local:reasoning` high, β `_local:reasoning_2` high, γ `_local:reasoning_3` high. Best for power users with diverse local models spanning all four categories.

#### Local Tier (`local`)

Balanced offline preset using reasoning + code-gen + lightweight + vision:

| Role | Placeholder | Resolves to |
|------|------------|-------------|
| orchestrator | `_local:code-gen` | Best local code-gen model |
| oracle | `_local:reasoning` | Best local reasoning model |
| librarian | `_local:lightweight` | Best local lightweight model |
| explorer | `_local:lightweight` | Best local lightweight model |
| designer | `_local:code-gen` | Best local code-gen model |
| fixer | `_local:code-gen` | Best local code-gen model |
| observer | `_local:vision` | Best local vision-capable lightweight model |

Council: α `_local:reasoning` high, β `_local:code-gen` high, γ `_local:lightweight` high. Best for balanced offline use with 3-party council diversity across model categories.

#### Local-Mini Tier (`local-mini`)

Minimal-diversity preset using code-gen + lightweight + vision:

| Role | Placeholder | Resolves to |
|------|------------|-------------|
| orchestrator | `_local:code-gen` | Best local code-gen model |
| oracle | `_local:code-gen` | Best local code-gen model |
| librarian | `_local:lightweight` | Best local lightweight model |
| explorer | `_local:lightweight` | Best local lightweight model |
| designer | `_local:code-gen` | Best local code-gen model |
| fixer | `_local:code-gen` | Best local code-gen model |
| observer | `_local:vision` | Best local vision-capable lightweight model |

Council: α `_local:code-gen` high, β `_local:lightweight` high, γ `_local:vision` high. Best for systems with only code-gen and lightweight models available.

#### Local-Nano Tier (`local-nano`)

Single-model preset using one code-gen model for all roles (except vision):

| Role | Placeholder | Resolves to |
|------|------------|-------------|
| orchestrator | `_local:code-gen` | Best local code-gen model |
| oracle | `_local:code-gen` | Best local code-gen model |
| librarian | `_local:code-gen` | Best local code-gen model |
| explorer | `_local:code-gen` | Best local code-gen model |
| designer | `_local:code-gen` | Best local code-gen model |
| fixer | `_local:code-gen` | Best local code-gen model |
| observer | `_local:vision` | Best local vision-capable lightweight model |

Council: α `_local:code-gen` high, β `_local:lightweight` high, γ `_local:vision` high. Best for single-model systems — council uses the code-gen model plus lightweight and vision for diversity.

#### Local-Solo Tier (`local-solo`)

Single-model preset using one omnicapable model for all roles:

| Role | Placeholder | Resolves to |
|------|------------|-------------|
| orchestrator | `_local:solo` | Best local solo model |
| oracle | `_local:solo` | Best local solo model |
| librarian | `_local:solo` | Best local solo model |
| explorer | `_local:solo` | Best local solo model |
| designer | `_local:solo` | Best local solo model |
| fixer | `_local:solo` | Best local solo model |
| observer | `_local:solo` | Best local solo model |

Council: α `_local:solo` max, β `_local:solo` high, γ `_local:solo` high. Diversity comes from variants, not different models. If no solo model exists, falls back to code-gen + vision (local-nano behavior).

> [!NOTE]
> Solo models require all four capabilities: completion + thinking + tools + vision. This maximizes per-request quality but needs enough VRAM. Users with limited VRAM should use local-mini or local-nano.

#### Local Model Classification

Placeholders are resolved by `configure-opencode-tier.py` using model name heuristics, size rules, `ollama show` parameter counts, and capability-aware classification:
- **reasoning**: models containing `r1`, `reasoning`, `deep-think`, `think`, `qwq`, `reflection`
- **code-gen**: models containing `coder`, `code`, `coding`, `devstral`, `codestral`, `deepseek-coder`, `qwen2.5-coder`, `qwen3-coder`, `codeqwen`
- **lightweight**: models containing `mini`, `small`, `tiny`, `phi`, `gemma:2`, `gemma3`, `smol`
- **vision**: subset of `lightweight` models that also have the `vision` capability (from `ollama show`)
- **solo**: models with all four capabilities (`completion` + `thinking` + `tools` + `vision`), purely capability-based (no name heuristics), sorted by parameter count descending

Indexed placeholders (`_local:<category>_2`) resolve to the second-best model in a category, ensuring council diversity. For example, `_local:code-gen_2` gives a different model from `_local:code-gen` when multiple code-gen models are available, or falls back to the second-best reasoning model if code-gen only has one entry.

Additional classification rules (applied after name heuristics):
- **Size rule**: models with `ollama list` SIZE < 12 GB are classified as `lightweight`
- **`ollama show` parameter-based**: unclassified models (≥ 12 GB, no name heuristic match) are classified via `ollama show` parameter count — parameters ≥ 7B → reasoning, parameters < 7B → code-gen (not lightweight)
- **Capability filtering**: after initial classification, each category is filtered by required capabilities parsed from `ollama show`:
  - `reasoning` requires `thinking` + `tools`
  - `code-gen`: name-heuristic-qualified models bypass capability checks; models classified via size/fallback rules require `thinking` + `completion`
  - `lightweight` requires `tools`
  - `vision` requires `tools` + `vision` (subset of lightweight)
  - `solo` requires `completion` + `thinking` + `tools` + `vision` (no name heuristics)
- **Code-gen reuse**: if no code-gen model is found via name heuristic, the reasoning model is reused for code-gen roles
- **Vision fallback**: if no vision-capable model exists, the best lightweight model is used with a warning
- **Indexed placeholders**: `_local:<category>_2` resolves to the second-best model in a category (e.g., `_local:code-gen_2` for council gamma diversity)

**Runtime warnings**: `configure-opencode-tier.py` warns when council councillors resolve to the same model (limited diversity), and reports total distinct models available across categories.

Switch tier: `scripts/configure-opencode-tier.py` <tier> (pro, pro-plus, pro-plus-anthropic, plus, plus-anthropic, anthropic, local-pro, local, local-mini, local-nano, local-solo)

Local Ollama models are appended to fallback chains by default. Use `--no-local-fallbacks` to omit them.

Default preset: auto-detected from available API keys during `run_once_14-configure-opencode.sh.tmpl`. Detection order: both OpenAI + Anthropic keys → pro-plus-anthropic, Anthropic only → anthropic, OpenAI only → plus, no keys but Ollama → local, nothing → pro. Local-pro, local-mini, local-nano, and local-solo are manual-only (set via `DOTFILES_OPENCODE_TIER`).

### Fallback Chains

Each cloud tier defines fallback chains per agent role (orchestrator, oracle, librarian, explorer, fixer, designer). The `anthropic` and all `local-*` tiers have **empty fallback chains by default** — they rely on their single-provider model hierarchy instead. The `plus-anthropic` tier has mixed OpenAI + Anthropic fallback chains.

Local Ollama models are appended to fallback chains by default (unless `--no-local-fallbacks` is passed). Discovered local models are appended **per-role** (not uniformly): oracle gets reasoning models, orchestrator/fixer/designer get code-gen models, librarian/explorer get lightweight models, observer gets vision-capable models. All indexed models matching a role's category are appended (not just the single best model).

### Local Ollama Fallback Policy

Local Ollama models are appended to fallback chains by default. Use `--no-local-fallbacks` to omit them.

#### Fallback Preset Selection (`--local-fallback-preset`)

By default, non-local tiers use the `local` tier's placeholder definitions to determine which local model categories to append to fallback chains. Use `--local-fallback-preset` to specify a different tier whose placeholders drive fallback selection:

```bash
# Use local-pro placeholders for richer fallback diversity
scripts/configure-opencode-tier.py --local-fallback-preset local-pro pro-plus

# Use local-mini placeholders (fewer categories) for lighter fallbacks
scripts/configure-opencode-tier.py --local-fallback-preset local-mini pro
```

For local tiers, `--local-fallback-preset` defaults to the current tier (so `local-pro` uses its own placeholders). For non-local tiers, it defaults to `local`.

#### Placeholder Overrides (`--local-fallback-placeholder`)

Use `--local-fallback-placeholder` to override which model fills a specific placeholder category slot, without changing the entire preset. This is a category→model override applied before role-level overrides:

```bash
# Use a specific model for the vision placeholder
scripts/configure-opencode-tier.py --local-fallback-placeholder vision=ollama/qwen3.5:9b-mlx pro-plus

# Multiple overrides
scripts/configure-opencode-tier.py --local-fallback-placeholder vision=ollama/qwen3.5:9b-mlx --local-fallback-placeholder reasoning=ollama/qwq:32b pro-plus
```

Format: `--local-fallback-placeholder <category>=<model>` where the left side is one of `reasoning`, `code-gen`, `lightweight`, `vision` and the right side is a model name (e.g., `ollama/qwen3.5:9b-mlx`).

#### Role Overrides (`--local-fallback-role`)

Use `--local-fallback-role` to override which specific model fills a specific agent role. This is a role-level override applied after placeholder overrides:

```bash
scripts/configure-opencode-tier.py --local-fallback-role observer=ollama/qwen3.5:9b-mlx pro-plus
```

Format: `--local-fallback-role <role>=<model>` where role is one of `orchestrator`, `oracle`, `librarian`, `explorer`, `fixer`, `designer`, `observer`.

#### Override Order

Overrides are applied in this order:
1. **Discovery**: local Ollama models are discovered and classified
2. **Placeholder overrides** (`--local-fallback-placeholder`): remap which category fills each placeholder slot
3. **Role overrides** (`--local-fallback-role`): remap which model fills each role
4. **Fallback chain append**: all indexed models matching the (possibly overridden) placeholder keys are appended per role

#### Multi-Model Fallback Appending

When local models are appended to fallback chains, all indexed variants matching the role's category are included — not just the single best model. For example, if both `reasoning` and `reasoning_2` placeholders are populated, both models appear in the oracle fallback chain.

#### Environment Variable Forwarding

The chezmoi bootstrap script (`run_once_14-configure-opencode.sh.tmpl`) forwards these env vars to `configure-opencode.py`:
- `DOTFILES_LOCAL_FALLBACK_PRESET` → `--local-fallback-preset`
- `DOTFILES_LOCAL_FALLBACK_PLACEHOLDERS` → comma-separated `--local-fallback-placeholder` args (e.g. `reasoning=code-gen,vision=lightweight`)
- `DOTFILES_LOCAL_FALLBACK_ROLES` → comma-separated `--local-fallback-role` args (e.g. `observer=ollama/qwen3.5:9b-mlx`)

#### Model Classification

| Role Category | Name Patterns                                                 | Required Capabilities | Fallback Priority |
|---------------|---------------------------------------------------------------|----------------------|-------------------|
| reasoning | `r1`, `reasoning`, `deep-think`, `think`, `qwq`, `reflection` | `thinking` + `tools` | oracle |
| code-gen | `coder`, `code`, `coding`, `devstral`, `codestral`, `laguna`  | `thinking` + `completion` (name-qualified bypass) | orchestrator, fixer, designer |
| lightweight | `mini`, `small`, `tiny`, `phi`, `smol`                        | `tools` | librarian, explorer |
| vision | subset of lightweight with `vision` capability                | `tools` + `vision` | observer |

Additional classification rules (applied after name heuristics):
- **Size rule**: models with `ollama list` SIZE < 12 GB are classified as `lightweight`
- **`ollama show` parameter-based**: unclassified models (≥ 12 GB, no name heuristic match) are classified via `ollama show` parameter count — parameters ≥ 7B → reasoning, parameters < 7B → code-gen (not lightweight)
- **Capability filtering**: after initial classification, categories are filtered by required capabilities parsed from `ollama show` output; name-qualified code-gen models bypass capability checks
- **Vision fallback**: if no vision-capable model exists, the best lightweight model is used with a warning
- **Code-gen reuse**: if no code-gen model is found via name heuristic, the reasoning model is reused for code-gen roles
- **Indexed placeholders**: `_local:<category>_2` resolves to the second-best model in a category, ensuring council diversity when the best model would duplicate another role

### Ollama Cloud Models

Ollama Cloud presets use models like `glm-5.1`, `kimi-k2.6`, `kimi-k2.7-code`, `deepseek-v4-pro`, `deepseek-v4-flash` — the exact set varies by tier and is defined in `oh-my-opencode-slim.json`. Ollama Cloud Pro accounts have a 3-slot concurrency limit (3 concurrent requests per account, regardless of how many distinct models are used). Model lists are not hardcoded in mozart-router config — the GenericOpenAIAdapter auto-discovers available models from each gateway's `/v1/models` endpoint.

### Variant Policy

Variants control reasoning effort per agent role. They are set in `oh-my-opencode-slim.json` and passed through to the model provider. Valid variants: `low`, `medium`, `high`, `max`, `xhigh` (and no variant = model default).

**Role → variant conventions:**

| Role | Variant | Rationale |
|------|---------|-----------|
| orchestrator | none (default) | Coordination, doesn't need boosted reasoning |
| oracle | `max` or `xhigh` | Strategic advisor, needs deepest reasoning |
| council | same as oracle | Configured as a preset agent; drives multi-model consensus |
| librarian | `low` | Lookup/search, lightweight |
| explorer | `low` | Pattern matching, lightweight |
| designer | `medium` | Needs balance of creativity and precision |
| fixer | `high` (code-specialized) or `low` (general) | Execution focused |
| observer | `low` or none | Visual extraction, lightweight |

**Model-specific variant notes:**

| Model | Default behavior | Oracle variant | Notes |
|-------|-----------------|----------------|-------|
| `nemotron-3-ultra` | standard | `max` | MoE frontier reasoning; oracle/council use max variant |
| `minimax-m3` | standard | `low` | Vision+reasoning; last-resort fallback for observer |
| `claude-opus-4-8` | `high` | `xhigh` | Opus defaults to high reasoning; oracle needs xhigh to push deeper |
| `claude-opus-4-6` | standard | — | Used for orchestrator (anthropic, pro-plus-anthropic) and council gamma (anthropic); no variant needed |
| `claude-sonnet-4-6` | standard | `high` | Sonnet for designer/fixer roles; variant `low` for fixer, `medium` for designer |
| `deepseek-v4-pro` | standard | `max` | Upstream opencode-go uses max for oracle |
| `gpt-5.5` | standard | `high` | Upstream openai preset uses high for oracle |
| `deepseek-v4-flash` | standard | `high` | Upstream uses high for fixer (code execution) |
| `glm-5.1` | standard | none | Upstream uses no variant for orchestrator |
| `kimi-k2.6` | standard | none | Upstream uses no variant for observer, `medium` for designer |
| `kimi-k2.7-code` | standard | none | Code-focused; mandatory thinking (cannot disable); ~30% lower thinking tokens vs kimi-k2.6 |
| `gpt-5.4-mini` | standard | `high` | Upstream uses high for fixer (code execution) |

---

## Adding a New Tier

1. Edit `scripts/configure-opencode-tier.py` — add a new `case` block with the tier name, preset, council config, and fallback chains (leave empty `{}` for single-provider tiers)
2. Add the preset definition to `configs/opencode/oh-my-opencode-slim.json` — define model, variant, skills, mcps, and council per agent role. For local-only tiers, use `_local:<category>` placeholders (reasoning/code-gen/lightweight/vision)
3. Add the `_tiers.<name>` block to `oh-my-opencode-slim.json` — define council agent entries, default_preset, presets, and fallback chains
4. Edit `scripts/configure-opencode.py` — add the tier to the preset validation case block, set `INCLUDE_ANTHROPIC`/`INCLUDE_OPENAI` flags as needed, configure provider generation
5. Edit `.chezmoiscripts/run_once_14-configure-opencode.sh.tmpl` — add tier detection logic for auto-detection
6. Update `AGENTS.md` tier table — add the new tier row
7. Update `README.md` tier table — add the new tier row
8. Update `configs/opencode/anthropic-models.json` if adding new Anthropic model IDs
9. Test: `scripts/configure-opencode-tier.py <tier>`, then `/preset <name>` in OpenCode

`configure-opencode-tier.py` is the single source of truth for tier→preset mapping.

---

## Adding an MCP Server

1. Create `configs/mcp/<server>.json` — MCP config JSON for the server
2. Add entry in `configs/mcp/global-mcps.json` — register which AI tools should receive this server config
3. Test with a single tool: `scripts/configure-mcp-tool.py <tool> <server>`
4. Regenerate all configs: `scripts/configure-mcp-all.py`

Notes:
- `idea.json` uses SSE transport by default — set `IJ_MCP_TRANSPORT=stdio` for stdio mode
- `sentry.json` passes `SENTRY_ACCESS_TOKEN` via `env`, not CLI args — don't expose tokens in command strings
- `run_once_13-configure-mcp.sh.tmpl` sources `detect-ij-mcp.py` output before the gate check

---

## CodeGraph Integration

[CodeGraph](https://github.com/colbymchenry/codegraph) is a local-first semantic code index + MCP server. It builds a SQLite-backed knowledge graph (symbols, edges, files, FTS search) so AI agents can answer structural questions without grepping through files.

### Key Properties

- **Zero-config**: No config file, no API keys, fully local
- **MCP server**: `codegraph serve --mcp` (stdio transport)
- **Per-project setup**: `codegraph init -i` creates `.codegraph/` with a SQLite index
- **Install**: `npm i -g @colbymchenry/codegraph`
- **Agent auto-config**: `codegraph install -y --target auto --location global` (idempotent, skips already-configured agents)
- **⚠️ Bare `codegraph` triggers the interactive installer** — use `codegraph status`, `codegraph init`, `codegraph install`, etc. instead of running `codegraph` with no arguments

### Files

| File | Purpose |
|------|---------|
| `configs/mcp/codegraph.json` | MCP template (command + args) |
| `configs/mcp/global-mcps.json` | Registry entry for all tools + project template |
| `scripts/install-opencode.sh` | Step 6: npm install codegraph + `codegraph install -y` agent auto-config |
| `.chezmoiscripts/run_once_10-install-opencode-plugins.sh.tmpl` | chezmoi-installed codegraph CLI + agent auto-config |
| `scripts/configure-opencode-project.py` | Step 3: `codegraph init -i` per project |

### MCP Config

```json
{
  "name": "codegraph",
  "type": "command",
  "command": "codegraph",
  "args": ["serve", "--mcp"]
}
```

### Agent Auto-Config

`codegraph install -y --target auto --location global` auto-configures CodeGraph MCP for all detected agents (Claude Code, Cursor, Codex CLI, opencode, Hermes Agent, Gemini CLI, Antigravity IDE). It is idempotent — shows "Unchanged" for already-configured agents.

This runs automatically in:
- `scripts/install-opencode.sh` (step 6, after npm install)
- `.chezmoiscripts/run_once_10-install-opencode-plugins.sh.tmpl`

Agents managed by our `configure-mcp-all.py` (opencode, cursor, codex, gemini, ai/air/junie) get codegraph via template. Agents NOT in our registry (Claude Code, Hermes, Antigravity) get codegraph via the `codegraph install` auto-config.

### Project Setup

`configure-opencode-project.py` runs `codegraph init -i` as step 3 by default. Skip it with `--steps`:

```bash
# Default: opencode.json + tier + codegraph
scripts/configure-opencode-project.py --preset pro-plus

# Add MCP configs for other AI platforms
scripts/configure-opencode-project.py --preset pro-plus --all-mcps

# Skip codegraph init
scripts/configure-opencode-project.py --preset pro-plus --steps opencode,tier

# Only opencode.json (fresh project, minimal)
scripts/configure-opencode-project.py --preset pro-plus --steps opencode

# Just codegraph init (re-run only step 3)
scripts/configure-opencode-project.py --preset pro-plus --steps opencode,codegraph
```

Available steps: `opencode` (always included), `tier`, `codegraph`, `mcps`.

The `.codegraph/` directory is gitignored (local index, not versioned).

---

## Junie Model Groups ↔ Oh My OpenCode Sync

`configs/junie/model-groups.json` defines Junie model profiles that should stay aligned with `configs/opencode/oh-my-opencode-slim.json` presets. When changing one, update the other.

### Mapping Rule

| Junie field | oh-my-opencode-slim source | Notes |
|-------------|---------------------------|-------|
| `primaryModel` | `orchestrator` model | Strip provider prefix (e.g., `ollama-cloud/glm-5.1` → `glm-5.1`) |
| `fasterModel` | `librarian` model | Strip provider prefix; add `fasterProvider` if different from `provider` |
| `temperature` | Per-provider defaults | `ollama-cloud`: 0.7, `openai`: 1, `meridian`: 1, `ollama-local`: 0.6 |
| `modelTemperatures` | — (Junie-specific) | Model-family temperature map; applied per-role at profile generation time |

### Cross-Provider fasterModel

When the librarian model uses a different provider than the orchestrator, add a `fasterProvider` field to the group. The profile generator emits role-level `baseUrl`/`apiType`/`apiKey` overrides for the fasterModel:

```json
"pro-plus": {
  "provider": "ollama-cloud",
  "primaryModel": "glm-5.1",
  "fasterModel": "gpt-5.4-mini",
  "fasterProvider": "openai"
}
```

### Local Tier Placeholders

Local groups use `_local:<category>` placeholders (not hardcoded model names). These are resolved at profile generation time by `scripts/generate-jetbrains-profiles.py`, which imports `resolve_roles_from_list()` from `scripts/configure-opencode-tier.py`:

| Placeholder | Resolves to | Junie usage |
|-------------|-------------|-------------|
| `_local:reasoning` | Best local reasoning model | `local-pro` primaryModel |
| `_local:code-gen` | Best local code-gen model | `local`/`local-mini`/`local-nano` primaryModel |
| `_local:lightweight` | Best local lightweight model | `local-pro`/`local` fasterModel |
| `_local:vision` | Best vision-capable lightweight model | `local-mini` fasterModel |
| `_local:solo` | Best local solo model (all 4 caps) | `local-solo` primaryModel and fasterModel |

If a placeholder cannot be resolved (no local models in that category), the profile generator skips the group with a warning.

### Model Family Temperature Overrides

Junie recommends model-family-specific temperatures for optimal results. The `modelTemperatures` field in `model-groups.json` maps model name prefixes to recommended temperatures, applied via per-role `primaryModel.temperature` and `fasterModel.temperature` at profile generation time:

| Prefix | Temperature | Model families |
|--------|------------:|----------------|
| `anthropic` | 1 | Anthropic Claude |
| `claude` | 1 | Claude (alias) |
| `deepseek` | 0 | DeepSeek V3/V4 |
| `gemini` | 1 | Gemini |
| `gemma` | 1 | Gemma 2/3/4 |
| `glm` | 0.7 | GLM-4/5 |
| `gpt` | 1 | GPT |
| `kimi` | 0.8 | Kimi K2 |
| `mimo` | 0.3 | MiMo |
| `qwen` | 0.6 | Qwen 2.5/3/3.5/3.6 |

Prefix matching is case-insensitive and uses longest-prefix-wins. If no prefix matches, the fallback temperature is 0.7.

All generated profiles emit temperatures exclusively via per-role `primaryModel.temperature` and `fasterModel.temperature` — never a top-level `temperature` field. This ensures each model's temperature is self-documenting and independent.

### Deployment

After changing `model-groups.json`:

```bash
python3 scripts/configure-jetbrains-ai.py --models
```

This generates profiles in `~/.junie/models/` and cleans up stale files.

---

## Voice Plugin (opencode-voice)

OpenCode voice support is provided by [`@renjfk/opencode-voice`](https://github.com/renjfk/opencode-voice) — a TUI-only plugin that adds voice input (STT) and output (TTS) to the OpenCode terminal interface.

### Key Properties

- **TUI-only**: The plugin only hooks into the TUI, not the desktop app or VSCode extension
- **Configured in `tui.json`**: Separate from `opencode.json`; written by `configure-opencode-voice.py`
- **Tier-aware**: Voice LLM endpoint and STT backend are selected based on the active preset
- **Local-first**: Default uses local Ollama + whisper-cli; cloud STT is an upgrade when API keys are available

### Voice Config Generation

`scripts/configure-opencode-voice.py` writes `~/.config/opencode/tui.json` with a tier-aware voice plugin config. It is called automatically by `configure-opencode.py` after tier switching.

| Tier | Voice LLM | STT Backend |
|------|-----------|-------------|
| **local-pro** | Best local Ollama model (auto-detected) | whisper-cli (local) |
| **local** | Best local Ollama model (auto-detected) | whisper-cli (local) |
| **local-mini** | Best local Ollama model (auto-detected) | whisper-cli (local) |
| **local-nano** | Best local Ollama model (auto-detected) | whisper-cli (local) |
| **local-solo** | Best local Ollama model (auto-detected) | whisper-cli (local) |
| **pro** | `gemma4:31b` via Ollama Cloud | whisper-cli (local), OpenAI STT if key available |
| **pro-plus** | `gemma4:31b` via Ollama Cloud | whisper-cli (local), OpenAI STT if key available |
| **pro-plus-anthropic** | `gemma4:31b` via Ollama Cloud | whisper-cli (local), OpenAI STT if key available |
| **plus** | `gpt-5.4-mini` via OpenAI | OpenAI STT |
| **plus-anthropic** | `gpt-5.4-mini` via OpenAI | OpenAI STT |
| **anthropic** | Meridian proxy or `claude-haiku-4-5` | whisper-cli (local), OpenAI STT if key available |

**Meridian detection**: `is_meridian_configured()` from `constants.py` controls Meridian routing for voice. If it returns true, the Anthropic tier uses Meridian as the voice LLM endpoint. Otherwise it falls back to direct Anthropic API.

**Cloud STT upgrade**: When `OPENAI_API_KEY` is available, non-OpenAI tiers add `sttEndpoint`/`sttModel`/`sttApiKeyEnv` pointing to OpenAI's `/v1/audio/transcriptions`. Tiers already using OpenAI for the LLM use OpenAI STT by default.

**Local Ollama model selection**: All `local-*` tiers reuse `configure-opencode-tier.py`'s model discovery — they pick the best local model for voice based on capability heuristics (preferring audio/vision-capable models).

### STT/TTS Dependencies

Voice requires local STT/TTS tooling regardless of tier:

| Component | Install | Purpose |
|-----------|---------|---------|
| `whisper-cpp` | `brew install whisper-cpp` | Local speech-to-text |
| `sox` | `brew install sox` | Audio format conversion (required by whisper-cli) |
| `piper-tts` | `uv tool install piper-tts` | Local text-to-speech |
| Whisper model | Download to `~/.local/share/whisper-cpp/` | STT model file |
| Piper voice | Download to `~/.local/share/piper-voices/` | TTS voice file |

These are installed by `scripts/install-opencode.sh` step 8 (gated on `DOTFILES_RUN_VOICE_SETUP=1`).

### Model Defaults

| Component | Env Var | Default | Notes |
|-----------|---------|---------|-------|
| Whisper model | `DOTFILES_WHISPER_MODEL` | `ggml-large-v3-turbo.bin` | Best balance of accuracy (1.5 GiB) |
| Piper voice | `DOTFILES_PIPER_VOICE` | `en_US-lessac-high` | High-quality English voice |

Piper voice URL is constructed from components: `en_US-lessac-high` → `en/en_US/lessac/high/en_US-lessac-high.onnx`

### Voice Plugin Config Locations

| File | Purpose |
|------|---------|
| `~/.config/opencode/tui.json` | Voice plugin config (+ other TUI plugins) |
| `~/.local/share/whisper-cpp/` | Whisper model directory |
| `~/.local/share/piper-voices/` | Piper voice directory |
| `~/.local/bin/piper` | Piper TTS binary (installed by `uv tool install piper-tts`) |

### Environment Gating

Voice setup in `install-opencode.sh` is gated on `DOTFILES_RUN_VOICE_SETUP=1` (default: 0). The voice config writer (`configure-opencode-voice.py`) runs unconditionally — it only writes `tui.json` and always respects the active tier.

---

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
| `.chezmoiscripts/run_once_12-install-smallcode.sh.tmpl` | Chezmoi install (gated on `DOTFILES_RUN_SMALLCODE_SETUP`) |
| `.chezmoiscripts/run_once_17-configure-smallcode.sh.tmpl` | Chezmoi config (gated on `DOTFILES_RUN_SMALLCODE_SETUP`) |
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

SmallCode setup is gated on `DOTFILES_RUN_SMALLCODE_SETUP=1` (default: 1 in `.env.example`, but must be explicitly set in `~/.env`).

### Shell Wrapper

The `smallcode()` function in `aliases.sh` is a simple passthrough — no multiplexer detection needed (unlike `opencode`).

### Chezmoi Phase

Phase 12 (`run_once_12-install-smallcode.sh.tmpl`) installs the SmallCode CLI. It:

1. Checks `DOTFILES_RUN_SMALLCODE_SETUP` gate
2. Delegates to `scripts/install-smallcode.sh` (npm/bun global install + verification)

Phase 17 (`run_once_17-configure-smallcode.sh.tmpl`) runs after OpenCode config (phase 14). It:

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

---

## Multiplexer Integration

oh-my-opencode-slim supports tmux and zellij multiplexer integration for side-by-side editing with an AI agent pane.

### Configuration

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

### Launching

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

### Prerequisites

- **tmux**: Already in `Brewfile` (installed via `make deploy`)
- **zellij**: Already in `Brewfile` (installed via `make deploy`)
- **Shell wrapper**: `opencode` function in `dot_dotfiles/shell/aliases.sh`

### Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENCODE_PORT` | Random (49152–65535) | Port for multiplexer communication between OpenCode and the editor pane |

`OPENCODE_PORT` defaults to a random high port via `jot -r 1 49152 65535`, avoiding conflicts with other opencode instances (ACP servers, headless serve, etc.). Set it explicitly in `~/.env` or on the command line if you need a fixed port.

### Common Tasks

| Task | Command |
|------|---------|
| Launch OpenCode (auto-multiplexer) | `opencode` (inside tmux/zellij: port is automatic) |
| Launch with specific port | `OPENCODE_PORT=5000 opencode` |
| Change multiplexer layout | Edit `configs/opencode/oh-my-opencode-slim.json`, then `scripts/configure-opencode.py` |
| Switch multiplexer type | Edit `type` in slim.json (`auto`/`tmux`/`zellij`/`none`), then `scripts/configure-opencode.py` |

---

## Secrets Management

`~/.env` is the single source of truth for all secrets. Format: `KEY='VALUE'`. Templates use `{{ env "VAR" }}` syntax.

Prefer Makefile targets (`make diff`, `make dry-run`, `make deploy`) because they load `~/.env` in the same shell process as chezmoi. For one-off raw chezmoi commands, load env manually: `set -a; source ~/.env; set +a`.

Use `make env-check` to report drift between `dot_dotfiles/shell/.env.example` and `~/.env`. Use `make env-sync` to append newly documented keys to `~/.env` as commented examples without overwriting secrets.

### Migrating old `~/.credentials.sh` files

Convert old sourced credential files from `export KEY=value` to `KEY='VALUE'` lines in `~/.env`:

| Old pattern | New pattern |
|------------|-------------|
| `export GH_TOKEN=...` / `export GITHUB_TOKEN=...` | `GH_TOKEN='...'` |
| `export OPENAI_API_KEY=...` | `OPENAI_API_KEY='...'` |
| `export ANTHROPIC_API_KEY=...` | `ANTHROPIC_API_KEY='...'` |
| `export AWS_ACCESS_KEY_ID=...` | `AWS_ACCESS_KEY_ID='...'` |
| `export AWS_SECRET_ACCESS_KEY=...` | `AWS_SECRET_ACCESS_KEY='...'` |
| `export NPM_TOKEN=...` | `NPM_TOKEN='...'` |
| `export SENTRY_ACCESS_TOKEN=...` | `SENTRY_AUTH_TOKEN='...'` |

`GH_TOKEN` is canonical for GitHub. `SENTRY_AUTH_TOKEN` is canonical in `~/.env`; MCP generation injects it as `SENTRY_ACCESS_TOKEN` for the Sentry MCP server.

**Key template files and their env var dependencies:**

| Template File | Key Env Vars |
|--------------|--------------|
| `dot_gitconfig.tmpl` | `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`, `GPG_SIGNING_KEY`, `GITHUB_USER`, `GH_TOKEN` |
| `private_dot_npmrc.tmpl` | `NPM_TOKEN`, `GH_TOKEN` |
| `private_dot_aws/credentials.tmpl` | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` |
| `private_dot_vuescanrc.tmpl` | `VUESCAN_USER_ID`, `VUESCAN_LICENSE`, `VUESCAN_CUSTOMER`, `VUESCAN_EMAIL` |
| `private_dot_gnupg/gpg.conf.tmpl` | `GPG_SIGNING_KEY` |

**`~/.env.example`** in the repo documents all available keys.

---

## AI Agent Guidance Files

- `AGENTS.md` is applied by chezmoi to `~/AGENTS.md`.
- `CLAUDE.md` is applied by chezmoi to `~/CLAUDE.md` and should stay aligned with `AGENTS.md`.
- `~/.codex/AGENTS.md` and `~/.cursor/AGENTS.md` are created by `run_onchange_12-configure-secrets.sh.tmpl` and point at the home-level guidance.

When editing agent guidance, edit `AGENTS.md` first and keep `CLAUDE.md` consistent.

---

## Mozart Router Gateways

| Gateway | Adapter | API Key Env | Cloud Proxy Env | Notes |
|---------|---------|-------------|-----------------|-------|
| Ollama Cloud | GenericOpenAI | `OLLAMA_API_KEY` | `DOTFILES_USE_OLLAMA_CLOUD_PROXY` | Cloud-hosted Ollama models; routes through local daemon when proxy enabled and available |
| openai | GenericOpenAI | `OPENAI_API_KEY` | — | OpenAI GPT models |
| anthropic-meridian | GenericOpenAI | `MERIDIAN_API_KEY` | — | Meridian proxy for Anthropic models. Host/port configurable via `MERIDIAN_HOST`/`MERIDIAN_PORT` env vars (defaults: `127.0.0.1:3456`) |

Gateways support `baseUrlEnv` keys (resolved by `configure-mozart-router.py`, stripped from output). When the named env var is set, it overrides the hardcoded `baseUrl`.

`configure-mozart-router.py` is the sole writer of `~/.mozart/mozart.json` (the old `dot_mozart/mozart.json.tmpl` was removed to avoid dual-source conflicts). It resolves `baseUrlEnv` overrides at runtime before writing.

All gateways use the GenericOpenAI adapter which auto-discovers models. If an API key is not set, the gateway will be detected but connections will fail gracefully with a warning.

### Local Service Host/Port Overrides

Local services (Ollama, Meridian) support host/port env var overrides. Use `scripts/lib/constants.py` functions (`get_ollama_local_base_url()`, `get_meridian_base_url()`) which read these at runtime:

| Service | Host Env Var | Port Env Var | Default |
|---------|-------------|-------------|---------|
| Local Ollama | `OLLAMA_LOCAL_HOST` | `OLLAMA_LOCAL_PORT` | `localhost:11434` |
| Official Ollama | `OLLAMA_HOST` | — | `http://localhost:11434` | Scheme+host[:port]; overrides `OLLAMA_LOCAL_HOST`/`PORT` |
| Meridian proxy | `MERIDIAN_HOST` | `MERIDIAN_PORT` | `127.0.0.1:3456` |

#### Meridian Detection Helper

`is_meridian_configured()` in `constants.py` is the canonical way to check if Meridian proxy should be used. It returns `True` if `MERIDIAN_API_KEY` or `ANTHROPIC_BASE_URL` is set. All scripts that need to route through Meridian should use this helper instead of duplicating the detection logic.

### Unified Ollama Routing

When the local Ollama daemon is running **and** signed in (`ollama signin`), it transparently proxies `:cloud` models to ollama.com. This means a single `OLLAMA_HOST` URL can serve both local and cloud models — no separate `ollama-cloud` provider needed.

#### How It Works

1. `check_ollama_daemon()` in `constants.py` probes the local daemon:
   - `GET /api/tags` → is the daemon running?
   - `GET /api/me` → is it signed in for cloud? (returns 401 if not)
   - Gated by `DOTFILES_USE_OLLAMA_CLOUD_PROXY` (default: enabled)

2. When the daemon is **cloud-capable** (running + signed in):
   - All Ollama models (local + cloud) route through `OLLAMA_HOST`
   - Cloud model names get `:cloud` suffix (e.g., `glm-5.1` → `glm-5.1:cloud`)
   - OpenCode gets a single `ollama` provider instead of separate `ollama` + `ollama-cloud`
   - SmallCode, Voice, and Mozart all route through the local daemon

3. When the daemon is **not cloud-capable** (not running, not signed in, or proxy disabled):
   - Falls back to the current two-provider approach: `ollama` (local) + `ollama-cloud` (direct)
   - Cloud models use `OLLAMA_CLOUD_BASE_URL` (default: `https://ollama.com/v1`)

#### Key Functions

| Function | Location | Purpose |
|----------|----------|---------|
| `check_ollama_daemon()` | `constants.py` | Returns `(is_running, can_proxy_cloud)` |
| `should_use_ollama_cloud_proxy()` | `constants.py` | Checks `DOTFILES_USE_OLLAMA_CLOUD_PROXY` env var |
| `get_ollama_base_url()` | `constants.py` | Returns local URL when cloud-capable, else direct cloud URL |
| `is_ollama_cloud_model(name)` | `discover_models.py` | Checks `:cloud` or `-cloud` suffix |
| `list_cloud_ollama_models()` | `discover_models.py` | Lists only cloud models from `ollama list` |

#### `:cloud` Suffix Handling

The `:cloud` suffix is appended **only** when routing through the local daemon. Direct cloud API calls use plain model names.

| Routing | Model Name | Example |
|---------|-----------|---------|
| Local daemon (cloud-capable) | `model:cloud` | `glm-5.1:cloud` |
| Direct cloud API (`ollama.com/v1`) | `model` (plain) | `glm-5.1` |
| Local model (any routing) | `model` (plain) | `qwen3-coder:14b` |

#### Scripts Affected

| Script | Change |
|--------|--------|
| `configure-opencode.py` | Merges `ollama` + `ollama-cloud` into single provider when cloud-capable |
| `configure-opencode-tier.py` | Discovers cloud models, appends `:cloud` suffixes to fallback chains |
| `configure-smallcode.py` | Routes `ollama-cloud/` models through local daemon with `:cloud` suffix |
| `configure-opencode-voice.py` | Routes voice through local daemon with `:cloud` suffix when capable |
| `configure-mozart-router.py` | Routes `ollama-cloud` gateway through local daemon when proxy enabled |

#### Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `DOTFILES_USE_OLLAMA_CLOUD_PROXY` | `1` (enabled) | Set to `0` or `false` to disable unified routing |
| `OLLAMA_HOST` | `http://localhost:11434` | Official Ollama env var for daemon URL |
| `OLLAMA_CLOUD_BASE_URL` | `https://ollama.com/v1` | Direct cloud URL (fallback when no local daemon) |

#### Disabling Unified Routing

To force the two-provider approach (separate local + cloud):

```bash
DOTFILES_USE_OLLAMA_CLOUD_PROXY=0 scripts/configure-opencode.py
```

This disables the daemon probe and always uses direct cloud URLs for cloud models.

### Provider Base-URL Overrides

Official SDK environment variables override hardcoded provider URLs across all scripts. When set, these take priority over `BASE_URLS` defaults in `constants.py`:

| Env Var | Provider | Default | Notes |
|---------|----------|---------|-------|
| `ANTHROPIC_BASE_URL` | Anthropic | `https://api.anthropic.com/v1` | Also signals Meridian usage when set |
| `OPENAI_BASE_URL` | OpenAI | `https://api.openai.com/v1` | OpenAI SDK standard |
| `OLLAMA_CLOUD_BASE_URL` | Ollama Cloud | `https://ollama.com/v1` | Non-standard (our env var, not official SDK) |

Resolution in `constants.py` via `get_provider_base_url(provider)`: env var override → `BASE_URLS` default → local Ollama fallback.

### JSON Config Override Convention

`model-groups.json` and `mozart.json` support these override keys:

| Key | Format | Purpose |
|-----|--------|---------|
| `baseUrlEnv` | Env var name (string) | If named env var is set, overrides `baseUrl` |
| `cloudProxyEnv` | Env var name (string) | If named env var is truthy and local Ollama can proxy cloud, overrides `baseUrl` to local daemon URL and removes `apiKeyEnv` |
| `hostEnvAlt` | Env var name (string) | Alt host env var (e.g., `OLLAMA_HOST`) with scheme+host[:port] format; takes priority over `hostEnv`/`portEnv` |

These keys are stripped from output configs (Mozart/Junie don't understand them) after resolving overrides.

---

## DCP Context Compaction

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

---

## Common Tasks

| Task | Command |
|------|---------|
| Switch AI tier | `scripts/configure-opencode-tier.py <tier>` (pro, pro-plus, pro-plus-anthropic, plus, plus-anthropic, anthropic, local-pro, local, local-mini, local-nano, local-solo) |
| Switch tier without local Ollama | `scripts/configure-opencode-tier.py --no-local-fallbacks <tier>` |
| Switch tier with local fallback role override | `scripts/configure-opencode-tier.py --local-fallback-role observer=ollama/qwen3.5:9b-mlx <tier>` |
| Switch tier with local fallback preset | `scripts/configure-opencode-tier.py --local-fallback-preset local-pro pro-plus` |
| Switch tier with local fallback placeholder override | `scripts/configure-opencode-tier.py --local-fallback-placeholder reasoning=code-gen <tier>` |
| Switch tier with multiple overrides | `scripts/configure-opencode-tier.py --local-fallback-preset local-pro --local-fallback-placeholder reasoning=code-gen --local-fallback-role observer=ollama/qwen3.5:9b-mlx pro-plus` |
| Regenerate all MCP configs | `scripts/configure-mcp-all.py` |
| Regenerate single MCP config | `scripts/configure-mcp-tool.py <tool> <server>` |
| Regenerate OpenCode config | `scripts/configure-opencode.py` |
| Regenerate project OpenCode config | `scripts/configure-opencode-project.py` |
| Regenerate project config (specific steps) | `scripts/configure-opencode-project.py --steps opencode,tier` |
| Regenerate project config (skip codegraph) | `scripts/configure-opencode-project.py --steps opencode,tier,mcps` |
| Regenerate project config (with MCPs for other tools) | `scripts/configure-opencode-project.py --all-mcps` |
| Initialize CodeGraph for project | `codegraph init -i` |
| Configure VibeGuard redaction | Edit `configs/opencode/vibeguard.config.json`, then `scripts/configure-opencode.py` |
| Configure voice plugin | `scripts/configure-opencode-voice.py --preset <tier>` |
| Configure SmallCode | `scripts/configure-smallcode.py --preset <tier>` |
| Configure SmallCode without local models | `scripts/configure-smallcode.py --preset pro --no-local-fallbacks` |
| Disable unified Ollama routing | `DOTFILES_USE_OLLAMA_CLOUD_PROXY=0 scripts/configure-opencode.py` |
| Install SmallCode CLI | `scripts/install-smallcode.sh` |
| Regenerate SmallCode MCP config | `scripts/configure-smallcode.py --preset <tier>` (MCP written directly) |
| Launch OpenCode with multiplexer | `opencode` (auto-detects tmux/zellij) |
| Configure multiplexer layout | Edit `configs/opencode/oh-my-opencode-slim.json`, then `scripts/configure-opencode.py` |
| Configure Mozart router | `scripts/configure-mozart-router.py` |
| Add Meridian to OpenCode | `scripts/configure-meridian.py` |
| Configure JetBrains AI (models, dirs, MCP) | `scripts/configure-jetbrains-ai.py --all` |
| Configure JetBrains workspace dirs | `scripts/configure-jetbrains-workspace.py` |
| Setup AI env files | `scripts/configure-ai.py` |
| Install OpenCode plugins | `scripts/install-opencode.sh` |
| Update npm globals across nvm | `scripts/update-nvm-globals.sh` |
| Verify Brewfile completeness | `scripts/verify-brewfile-completeness.py` |
| Check env template drift | `make env-check` |
| Append missing env keys as comments | `make env-sync` |
| Preview pending changes | `make diff` |
| Dry-run apply | `make dry-run` |
| Apply all dotfiles | `make deploy` |

---

## Operational Philosophy

- **Local-repo trust:** No age/GPG encryption because the repo is local-only
- **Idempotent scripts:** Every `run_once_*` must be safe to re-run
- **Graceful degradation:** Network failures in chezmoi scripts warn, never abort
- **Single sources of truth:** `scripts/` for shell logic, `configs/` for JSON configs, `~/.env` for secrets, `AGENTS.md` for agent docs
- **Homebrew-agnostic paths:** Always `$(brew --prefix)` — never hardcode platform paths
- **2-space shell indent:** Enforced by `.editorconfig` + `shfmt`
- **Commits:** `feat/fix/refactor/chore/docs` with scopes: `dotfiles`, `brew`, `secrets`, `scripts`, `templates`, `infra`, `agents`
