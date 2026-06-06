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
├── .chezmoiscripts/              # 16 ordered run scripts (defaults → packages → config)
│   ├── # Phase 1: System defaults (01–07)
│   ├── # Phase 2: Package installation (08–11)
│   └── # Phase 3: Tool configuration (12–16)
├── configs/
│   ├── junie/model-groups.json   # Junie model profile definitions
│   ├── mcp/                      # MCP server configs
│   │   ├── betterstack.json
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
│   │   ├── logger.py              # Central Python logging module
│   │   ├── env.py                 # load_env() and token aliases in Python
│   │   ├── ai_mcps.py             # Filter template globs in Python
│   │   ├── ai_dirs.py             # Python-ported platform-independent directory setups
│   │   ├── ai_models.py           # Python-ported model prefix mappings & temperatures
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
│   ├── generate-jetbrains-profiles.py # Generate model profiles JSON files
│   ├── get-tools.py               # Get MCP tool registry keys
│   ├── install-opencode.sh        # Install OpenCode plugins and tools
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
# Use scripts/lib/discover_models.py for Ollama discovery in Python scripts
# Use scripts/lib/ai_models.py for model prefix mappings and temperatures
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

Seven tiers defined in `scripts/configure-opencode-tier.py` (source of truth):

| Tier | Providers | Best For |
|------|-----------|----------|
| **pro** | Ollama Cloud (glm-5.1 orchestrator, nemotron-3-ultra council) | Daily coding, budget mode |
| **pro-plus** | Ollama Cloud + OpenAI (`gpt-5.5`) | General development |
| **pro-plus-anthropic** | Anthropic + Ollama Cloud + OpenAI | Heavy orchestration |
| **plus** | OpenAI only (`gpt-5.5`, `gpt-5.4-mini`) | OpenAI-first workflow |
| **plus-anthropic** | OpenAI + Anthropic (no Ollama Cloud) | OpenAI + Anthropic hybrid |
| **anthropic** | Anthropic only | Anthropic-first workflow |
| **local** | Local Ollama only | Fully offline/air-gapped |

Cloud presets (pro, pro-plus, pro-plus-anthropic) use Ollama Cloud models including `nemotron-3-ultra`, `minimax-m3`, `glm-5.1`, `kimi-k2.6`, `deepseek-v4-pro`, `deepseek-v4-flash`. The `plus` preset uses OpenAI models exclusively. The `plus-anthropic` preset uses OpenAI and Anthropic models without Ollama Cloud. The `anthropic` preset uses only Anthropic models. The `local` preset uses `_local:<category>` placeholders resolved at runtime.

#### Anthropic Tier (`anthropic`)

Anthropic-only preset with no OpenAI or Ollama Cloud providers:

| Role | Model | Variant |
|------|-------|---------|
| orchestrator | `claude-opus-4-7` | — |
| oracle | `claude-opus-4-7` | xhigh |
| librarian | `claude-haiku-4-5` | low |
| explorer | `claude-haiku-4-5` | low |
| designer | `claude-sonnet-4-6` | medium |
| fixer | `claude-sonnet-4-6` | low |
| observer | `claude-haiku-4-5` | low |

Council agent is defined inside each preset's agent list; alpha `claude-opus-4-7`, beta `claude-sonnet-4-6`, gamma `claude-haiku-4-5`. Empty fallback chains by default — local Ollama models are appended automatically unless `--no-local-fallbacks` is passed.

#### Plus-Anthropic Tier (`plus-anthropic`)

OpenAI + Anthropic preset with no Ollama Cloud providers:

| Role | Model | Variant |
|------|-------|---------|
| orchestrator | `openai/gpt-5.5` | — |
| oracle | `anthropic/claude-opus-4-7` | xhigh |
| librarian | `openai/gpt-5.4-nano` | low |
| explorer | `anthropic/claude-haiku-4-5` | low |
| designer | `anthropic/claude-sonnet-4-6` | medium |
| fixer | `openai/gpt-5.4-mini` | high |
| observer | `anthropic/claude-haiku-4-5` | low |

Council agent is defined inside each preset's agent list; alpha `claude-opus-4-7`, beta `gpt-5.5`, gamma `gpt-5.4`. Fallback chains mix OpenAI + Anthropic models per role — local Ollama models are appended automatically unless `--no-local-fallbacks` is passed.

#### Local Tier (`local`)

Fully offline preset using `_local:<category>` placeholders:

| Role | Placeholder | Resolves to |
|------|------------|-------------|
| orchestrator | `_local:code-gen` | Best local code-gen model |
| oracle | `_local:reasoning` | Best local reasoning model |
| librarian | `_local:lightweight` | Best local lightweight model |
| explorer | `_local:lightweight` | Best local lightweight model |
| designer | `_local:code-gen` | Best local code-gen model |
| fixer | `_local:code-gen` | Best local code-gen model |
| observer | `_local:vision` | Best local vision-capable lightweight model |

Placeholders are resolved by `configure-opencode-tier.py` using model name heuristics, size rules, `ollama show` parameter counts, and capability-aware classification:
- **reasoning**: models containing `r1`, `reasoning`, `deep-think`, `think`, `qwq`, `reflection`
- **code-gen**: models containing `coder`, `code`, `coding`, `devstral`, `codestral`, `deepseek-coder`, `qwen2.5-coder`, `qwen3-coder`, `codeqwen`
- **lightweight**: models containing `mini`, `small`, `tiny`, `phi`, `gemma:2`, `gemma3`, `smol`
- **vision**: subset of `lightweight` models that also have the `vision` capability (from `ollama show`)

Indexed placeholders (`_local:<category>_2`) resolve to the second-best model in a category, ensuring council diversity. For example, `_local:code-gen_2` gives a different model from `_local:code-gen` when multiple code-gen models are available, or falls back to the second-best reasoning model if code-gen only has one entry.

Additional classification rules (applied after name heuristics):
- **Size rule**: models with `ollama list` SIZE < 12 GB are classified as `lightweight`
- **`ollama show` parameter-based**: unclassified models (≥ 12 GB, no name heuristic match) are classified via `ollama show` parameter count — parameters ≥ 7B → reasoning, parameters < 7B → code-gen (not lightweight)
- **Capability filtering**: after initial classification, each category is filtered by required capabilities parsed from `ollama show`:
  - `reasoning` requires `thinking` + `tools`
  - `code-gen` requires `thinking` + `completion`
  - `lightweight` requires `tools`
  - `vision` requires `tools` + `vision` (subset of lightweight)
- **Code-gen reuse**: if no code-gen model is found via name heuristic, the reasoning model is reused for code-gen roles
- **Vision fallback**: if no vision-capable model exists, the best lightweight model is used with a warning
- **Indexed placeholders**: `_local:<category>_2` resolves to the second-best model in a category (e.g., `_local:code-gen_2` for council gamma diversity)

Switch tier: `scripts/configure-opencode-tier.py` <tier> (pro, pro-plus, pro-plus-anthropic, plus, plus-anthropic, anthropic, local)

Local Ollama models are appended to fallback chains by default. Use `--no-local-fallbacks` to omit them.

Default preset: auto-detected from available API keys during `run_once_14-configure-opencode.sh.tmpl`. Detection order: both OpenAI + Anthropic keys → pro-plus-anthropic, Anthropic only → anthropic, OpenAI only → plus, no keys but Ollama → local, nothing → pro.

### Fallback Chains

Each cloud tier defines fallback chains per agent role (orchestrator, oracle, librarian, explorer, fixer, designer). The `anthropic` and `local` tiers have **empty fallback chains by default** — they rely on their single-provider model hierarchy instead. The `plus-anthropic` tier has mixed OpenAI + Anthropic fallback chains.

Local Ollama models are appended to fallback chains by default (unless `--no-local-fallbacks` is passed). Discovered local models are appended **per-role** (not uniformly): oracle gets reasoning models, orchestrator/fixer/designer get code-gen models, librarian/explorer get lightweight models, observer gets vision-capable models.

### Local Ollama Fallback Policy

Local Ollama models are appended to fallback chains by default. Use `--no-local-fallbacks` to omit them.

Models are classified into four categories using name heuristics, size rules, and `ollama show` parameter counts and capabilities:

| Role Category | Name Patterns                                                 | Required Capabilities | Fallback Priority |
|---------------|---------------------------------------------------------------|----------------------|-------------------|
| reasoning | `r1`, `reasoning`, `deep-think`, `think`, `qwq`, `reflection` | `thinking` + `tools` | oracle |
| code-gen | `coder`, `code`, `coding`, `devstral`, `codestral`, `laguna`  | `thinking` + `completion` | orchestrator, fixer, designer |
| lightweight | `mini`, `small`, `tiny`, `phi`, `smol`                        | `tools` | librarian, explorer |
| vision | subset of lightweight with `vision` capability                | `tools` + `vision` | observer |

Additional classification rules (applied after name heuristics):
- **Size rule**: models with `ollama list` SIZE < 12 GB are classified as `lightweight`
- **`ollama show` parameter-based**: unclassified models (≥ 12 GB, no name heuristic match) are classified via `ollama show` parameter count — parameters ≥ 7B → reasoning, parameters < 7B → code-gen (not lightweight)
- **Capability filtering**: after initial classification, categories are filtered by required capabilities parsed from `ollama show` output
- **Vision fallback**: if no vision-capable model exists, the best lightweight model is used with a warning
- **Code-gen reuse**: if no code-gen model is found via name heuristic, the reasoning model is reused for code-gen roles
- **Indexed placeholders**: `_local:<category>_2` resolves to the second-best model in a category, ensuring council diversity when the best model would duplicate another role

### Ollama Cloud Models

Ollama Cloud presets use models like `glm-5.1`, `kimi-k2.6`, `deepseek-v4-pro`, `deepseek-v4-flash` — the exact set varies by tier and is defined in `oh-my-opencode-slim.json`. Ollama Cloud Pro accounts have a 3-slot concurrency limit (3 concurrent requests per account, regardless of how many distinct models are used). Model lists are not hardcoded in mozart-router config — the GenericOpenAIAdapter auto-discovers available models from each gateway's `/v1/models` endpoint.

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
| `claude-opus-4-7` | `high` | `xhigh` | Opus defaults to high reasoning; oracle needs xhigh to push deeper |
| `deepseek-v4-pro` | standard | `max` | Upstream opencode-go uses max for oracle |
| `gpt-5.5` | standard | `high` | Upstream openai preset uses high for oracle |
| `deepseek-v4-flash` | standard | `high` | Upstream uses high for fixer (code execution) |
| `glm-5.1` | standard | none | Upstream uses no variant for orchestrator |
| `kimi-k2.6` | standard | none | Upstream uses no variant for observer, `medium` for designer |
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

| Gateway | Adapter | API Key Env | Notes |
|---------|---------|-------------|-------|
| Ollama Cloud | GenericOpenAI | `OLLAMA_API_KEY` | Cloud-hosted Ollama models |
| openai | GenericOpenAI | `OPENAI_API_KEY` | OpenAI GPT models |
| anthropic-meridian | GenericOpenAI | `MERIDIAN_API_KEY` | Meridian proxy for Anthropic models. Host/port configurable via `MERIDIAN_HOST`/`MERIDIAN_PORT` env vars (defaults: `127.0.0.1:3456`) |

All gateways use the GenericOpenAI adapter which auto-discovers models. If an API key is not set, the gateway will be detected but connections will fail gracefully with a warning.

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
| Switch AI tier | `scripts/configure-opencode-tier.py <tier>` (pro, pro-plus, pro-plus-anthropic, plus, plus-anthropic, anthropic, local) |
| Switch tier without local Ollama | `scripts/configure-opencode-tier.py --no-local-fallbacks <tier>` |
| Switch tier with local fallback role override | `scripts/configure-opencode-tier.py --local-fallback-role observer=ollama/qwen3.5:9b-mlx <tier>` |
| Regenerate all MCP configs | `scripts/configure-mcp-all.py` |
| Regenerate single MCP config | `scripts/configure-mcp-tool.py <tool> <server>` |
| Regenerate OpenCode config | `scripts/configure-opencode.py` |
| Regenerate project OpenCode config | `scripts/configure-opencode-project.py` |
| Configure VibeGuard redaction | Edit `configs/opencode/vibeguard.config.json`, then `scripts/configure-opencode.py` |
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
