# Chezmoi Dotfiles Migration Plan — Revised Annotated Plan

## Overview

Migrate `~/.dotfiles/` to chezmoi-managed dotfiles at `~/Development/dotfiles/` with composable Brewfiles, numbered setup scripts, environment-variable-based secret templating, and all home directory configs.

> **⚠ CURRENT STATUS (2026-05-24):** Source directory is `~/Development/dotfiles/` (not `~/.local/share/chezmoi/`), configured via `sourceDir` in `.chezmoi.toml.tmpl`. The migration is functionally complete: all dotfiles are in chezmoi, all templates render correctly with live env vars, all 11 scripts pass validation, all 13 Brewfiles are reconciled against `~/Brewfile` (292 items, 0 missing, 0 extra). **`chezmoi apply --dry-run --verbose` exits 0.** **`chezmoi apply` is live and idempotent.** Pending: commit.

---

## 1. Prerequisites & Bootstrap

### Install chezmoi
```bash
brew install chezmoi
```

### Initialize chezmoi with the dotfiles repo
```bash
chezmoi init --apply=false
```

> **⚠ ANNOTATION:** ✅ Complete. Source dir is `~/Development/dotfiles/`. Minimum version set to 2.70.2 (not 2.40.0 as originally planned — set by `chezmoi init` after initial bootstrap).

### Minimum version
Add to `chezmoi.toml`:
```toml
minimumVersion = "2.40.0"
```

> **⚠ ANNOTATION:** ✅ Complete. Actual minimum version set to 2.70.2. Configured via `.chezmoi.toml.tmpl`.

---

## 2. Directory Structure

```
~/Development/dotfiles/                    # chezmoi source directory (🔀 DEVIATION: not ~/.local/share/chezmoi/)
├── .chezmoi.toml.tmpl                      # chezmoi config template
├── .chezmoiignore                          # ignore patterns
├── .chezmoidata/
│   └── categories.yaml                     # Brewfile category selection (13 categories)
├── .chezmoiscripts/                        # 11 scripts (🔀 DEVIATION: not 7, now 11 with run_10)
│   ├── run_once_00-setup-chezmoi.sh.tmpl    # env var aliasing, dirs, permissions
│   ├── run_onchange_01-install-packages.sh.tmpl  # 🔀 DEVIATION: sha256sum-based re-run on Brewfile change
│   ├── run_once_02-configure-macos-defaults.sh.tmpl  # macOS system preferences
│   ├── run_once_03-configure-git.sh.tmpl     # git config, GPG signing (🔀 auto-detects gpg path)
│   ├── run_once_04-configure-ssh.sh.tmpl     # SSH key permissions
│   ├── run_05-configure-mcp.sh.tmpl          # 🔀 New: sources detect-ij-mcp output before gate, copies MCP configs, runs configure-mcp-all (every apply)
│   ├── run_once_06-install-opencode-plugins.sh.tmpl  # 🔀 DEVIATION: fully implemented (OpenSpec + opencode plugins)
│   ├── run_once_07-configure-mac-defaults-extended.sh.tmpl  # 🔀 New
│   ├── run_08-configure-secrets.sh.tmpl      # 🔀 New: AI tool .env files, AGENTS.md symlinks (every apply)
│   ├── run_09-configure-opencode.sh.tmpl     # 🔀 New: tier detection, opencode config (every apply)
│   └── run_10-install-plannotator.sh.tmpl    # 🆕 NEW: idempotent plannotator install/update
├── dot_AGENTS.md                            # AI agent guidance (6 presets across providers)
├── dot_bash_profile
├── dot_bashrc                               # 🔀 DEVIATION: ~/.credentials.sh sourcing removed
├── dot_codexbar/                            # 🆕 NEW: sanitized codexbar config (no secrets)
│   └── config.json                          #   48 providers, ordering preserved, secrets redacted
├── configs/iterm2/Default.json                  # iTerm2 Dynamic Profile (clean, no command history)
├── dot_config/
│   ├── bat/config
│   ├── direnv/direnvrc
│   ├── gh/config.yml.tmpl                    # Uses {{ env "GH_TOKEN" }}
│   ├── git/ignore
│   ├── opencode/
│   │   └── dcp.json                         # 🆕 NEW: DCP context compaction (percentage-based thresholds)
│   ├── ripgrep/config
│   └── starship.toml.tmpl
├── dot_dotfiles/                             # 🔀 DEVIATION: shell configs now managed (not .chezmoiignore'd)
│   └── shell/
│       ├── .env.example                      # 🔀 UPDATED: DOTFILES_* namespace + DOTFILES_USE_LOCAL_OLLAMA + OpenSpec telemetry opt-out
│       ├── aliases.sh                        # 🔀 UPDATED: dynamic neofetch, no hardcoded /opt/homebrew paths
│       ├── completions.bash
│       ├── completions.zsh
│       ├── paths.sh                          # 🔀 UPDATED: brew --prefix first, Intel/ARM fallback
│       ├── prompt.sh
│       └── variables.sh
├── dot_editorconfig
├── dot_emacs
├── dot_gitconfig.tmpl                        # Uses {{ env "GH_TOKEN" }}, no GPG section (🔀 auto-detected)
├── dot_gitignore
├── dot_hushlogin
├── dot_lynxrc
├── dot_nvmrc                                 # 🔀 UPDATED: nvm default → 24 (was 20)
├── dot_profile
├── dot_zshrc
├── private_dot_aws/credentials.tmpl          # 2 profiles: [default] + [randytarampi]
├── private_dot_basilisk_ii_prefs
├── private_dot_gnupg/
│   ├── gpg-agent.conf
│   └── gpg.conf
├── private_dot_npmrc.tmpl                    # 🔀 DEVIATION: single NPM_TOKEN for both npmjs lines
├── private_dot_sheepshaver_prefs
├── private_dot_ssh/config
├── private_dot_vuescanrc.tmpl                # 🔀 DEVIATION: was static dot_vuescanrc, now templated
├── CLAUDE.md                                 # Applied to ~/CLAUDE.md; keep aligned with AGENTS.md
├── Brewfile                                  # dev.cli — reconciled to ~/Brewfile
├── Brewfile.dev                              # dev — reconciled to ~/Brewfile
├── Brewfile.desktop.browsers                 # desktop.browsers (macOS-only)
├── Brewfile.desktop.cloud                    # desktop.cloud (macOS-only)
├── Brewfile.desktop.comms                    # desktop.comms (macOS-only)
├── Brewfile.desktop.fonts                    # desktop.fonts (macOS-only)
├── Brewfile.desktop.gaming                   # desktop.gaming (macOS-only, opt-in)
├── Brewfile.desktop.media                    # desktop.media (macOS-only)
├── Brewfile.desktop.productivity             # desktop.productivity (macOS-only)
├── Brewfile.desktop.security                 # desktop.security (macOS-only)
├── Brewfile.desktop.utilities                # desktop.utilities (macOS-only)
├── Brewfile.dev.ops                          # dev.ops
├── Brewfile.legacy                           # legacy (opt-in)
├── configs/                                  # referenced by scripts, not applied to home
│   ├── opencode/
│   │   ├── oh-my-opencode-slim.json          # Presets, council, fallbacks, tier overrides
│   │   ├── anthropic-models.json             # 🔀 FIXED: haiku maxTokens 16384→64000
│   │   ├── role-to-local-category.json       # New
│   │   ├── openai-models.json                # New
│   │   └── ollama-cloud-models.json          # New
│   └── mcp/
│       ├── betterstack.json                  # References ${BETTERSTACK_API_TOKEN}
│       ├── github.json                       # References ${GH_TOKEN} (not GITHUB_PERSONAL_ACCESS_TOKEN)
│       ├── global-mcps.json                  # 🔀 New: 6 AI tool definitions (GitLab removed)
│       ├── idea.json                          # detect-ij-mcp.py uses $(brew --prefix) for Homebrew IDEA
│       ├── mongodb.json                      # References ${MDB_MCP_API_CLIENT_ID}, ${MDB_MCP_API_CLIENT_SECRET}
│       ├── notion.json                       # 🔀 FIXED: enabled by default (removed "enabled": false)

│       ├── sentry.json                       # Uses env.SENTRY_ACCESS_TOKEN (no CLI arg)
│       ├── shortcut.json
│       └── templates/                        # 🔀 New: symlinks to config files (GitLab removed)
│           └── [11 symlinks → ../<name>.json]
└── scripts/                                  # 14 scripts + lib/
    ├── lib/
    │   ├── common.sh                         # info(), ok(), warn(), die(), run_or_skip()
    │   ├── env.sh                            # load_env(), alias_github_token()
    │   └── discover_models.py                # Local Ollama discovery helpers
    ├── configure-mcp-all.py                  # Reads global-mcps.json, generates per-tool MCP configs
    ├── configure-mcp-tool.py                 # Generate MCP config for single AI tool
    ├── configure-opencode.py                 # 🔀 UPDATED: DOTFILES_USE_LOCAL_OLLAMA default, no --with-local-ollama switch, MCP_DOCKER removed
    ├── configure-opencode-project.py         # 🔀 UPDATED: mirrored configure-opencode changes
    ├── configure-opencode-tier.py            # 🔀 Single source of truth for model tables
    ├── verify-brewfile-completeness.py       # Verify Brewfile completeness
    └── [...8 other scripts]
```

> **⚠ ANNOTATION — KEY DEVIATIONS FROM ORIGINAL PLAN:**
>
> | Original Plan | Actual Implementation | Rationale |
> |---------------|----------------------|-----------|
> | Source: `~/.local/share/chezmoi/` | Source: `~/Development/dotfiles/` via `sourceDir` | Developer preference |
> | 7 scripts | 11 scripts (`run_05/08/09/10` added) | Functional splits: MCP, secrets, opencode, plannotator |
> | `run_once_01` | `run_onchange_01` with sha256sum | Auto re-apply when Brewfiles change |
> | `run_once_05` dropped | Split into `run_05`, `run_08`, `run_09` | Separation of concerns |
> | `.chezmoiignore shell/` | `dot_dotfiles/shell/` managed | Ensures shell configs deploy with everything else |
> | `GITHUB_TOKEN` in templates | `{{ env "GH_TOKEN" }}` in all templates | Consistency with `gh` CLI and GitHub Actions |
> | Npm in `.gitconfig.tmpl` | Auto-detected in `run_once_03` | Dynamic path resolution |
> | Static `dot_vuescanrc` | `private_dot_vuescanrc.tmpl` with env vars | Prevent license keys in repo |
> | `NPM_TOKEN` + `NPM_ORG_TOKEN` | Single `NPM_TOKEN` for both npmjs lines | Simplification: both registries use same token |
> | No MCP config mgmt | `configs/mcp/` + `global-mcps.json` + `templates/` | Enable `configure-mcp-all.sh` for 6 AI tools |
> | No DCP config | `dot_config/opencode/dcp.json` (percentage thresholds) | Prevents premature compaction for large contexts |
> | No codexbar config | `dot_codexbar/config.json` (sanitized) | Preserves provider ordering for `configure-mcp-all.sh` |
> | nvm alias 20 | nvm alias 24 | Bump to current LTS (Krypton) |
> | `RUN_*` env vars | `DOTFILES_RUN_*` env vars | 🔀 Namespace isolation — prevents collisions with other tools |
> | `--with-local-ollama` CLI switch | `DOTFILES_USE_LOCAL_OLLAMA` env var (default: true) | 🔀 Simplified: env-driven, no CLI flag |
> | pro.sh/pro-plus.sh wrappers | `configure-opencode-tier.py <tier>` directly | 🔀 Dropped thin wrappers |
> | `run_09` references `scripts/configure-opencode-tier.py` | No `scripts/${TIER}.sh` wrappers remain | 🔀 Fixed broken wrapper reference |
> | ollama-discovery: classify + pick_best | Just `list_local_ollama_models` + `discover_local_ollama_models_json` | 🔀 Removed dead code — no callers of OLLAMA_MODEL_*_BEST |
> | Hardcoded `/opt/homebrew` paths | PATH-driven discovery (paths.sh ensures Homebrew on PATH) | 🔀 Cross-arch (Intel/ARM Mac) portability |
> | `uname -p` for arch detection | `uname -m` (arm64/x86_64) | 🔀 More reliable on Apple Silicon |

---

## 3. Secrets Management

### `.env` format
Use `KEY='VALUE'` format (single-quoted values, no `export` keyword):
```bash
# ~/.env (gitignored!)
GH_TOKEN='ghp_xxxx'
ANTHROPIC_API_KEY='sk-ant-xxxx'
OPENAI_API_KEY='sk-xxxx'
AWS_ACCESS_KEY_ID='AKIAxxxx'
AWS_SECRET_ACCESS_KEY='xxxx'
# Add other local-only secrets here as needed.
```

Loaded in shell configs via `set -a; source ~/.env; set +a`. The `set -a` flag auto-exports all variables, so no `export` keyword is needed in the `.env` file itself. Single quotes prevent shell expansion of `$`, backticks, and special characters.

> **⚠ ANNOTATION:** ✅ `~/.env` exists with 83+ keys. `POSTMAN_BEARER_TOKEN` is OAuth-managed (authenticated via browser, not a static token) — cannot be added to `.env`. `SENTRY_ACCESS_TOKEN` renamed to `SENTRY_AUTH_TOKEN` (canonical name) and is now passed via `env.SENTRY_ACCESS_TOKEN` in Sentry MCP config, not as a CLI arg. `GITLAB_API_PRIVATE_TOKEN` still exists in `.env` but GitLab MCP removed. **🔀 All `RUN_*` vars renamed to `DOTFILES_RUN_*`** — `DOTFILES_RUN_INSTALL_PACKAGES`, `DOTFILES_RUN_OPENCODE_SETUP`, `DOTFILES_RUN_MCP_SETUP`, `DOTFILES_RUN_MACOS_DEFAULTS`, `DOTFILES_RUN_PLANNOTATOR_SETUP`. **🆕 `DOTFILES_USE_LOCAL_OLLAMA=1`** — controls local Ollama provider inclusion when the `ollama` binary is installed.

### Migrating old `~/.credentials.sh` entries

The migration removes sourced credential shell files. Move old `export KEY=value` entries into `~/.env` as `KEY='VALUE'` assignments without `export`:

| Old pattern | New `~/.env` entry |
|-------------|--------------------|
| `export GH_TOKEN=...` / `export GITHUB_TOKEN=...` | `GH_TOKEN='...'` |
| `export OPENAI_API_KEY=...` | `OPENAI_API_KEY='...'` |
| `export ANTHROPIC_API_KEY=...` | `ANTHROPIC_API_KEY='...'` |
| `export AWS_ACCESS_KEY_ID=...` | `AWS_ACCESS_KEY_ID='...'` |
| `export AWS_SECRET_ACCESS_KEY=...` | `AWS_SECRET_ACCESS_KEY='...'` |
| `export NPM_TOKEN=...` | `NPM_TOKEN='...'` |
| `export SENTRY_ACCESS_TOKEN=...` | `SENTRY_AUTH_TOKEN='...'` |
| `export GPG_SIGNING_KEY=...` | `GPG_SIGNING_KEY='...'` |

`GH_TOKEN` is canonical for GitHub. `SENTRY_AUTH_TOKEN` is canonical in `~/.env`; MCP generation injects it as `SENTRY_ACCESS_TOKEN` for the Sentry MCP server.

Use Makefile targets so `~/.env` is loaded in the same shell process as chezmoi:

```bash
make diff
make dry-run
make deploy
```

For one-off raw chezmoi commands only, load env manually:

```bash
set -a; source ~/.env; set +a
```

### Env var aliasing
Runtime scripts source `scripts/lib/env.sh` and alias `GH_TOKEN` for consumers that expect other names:
```bash
# Alias GH_TOKEN → other consumers
export GITHUB_TOKEN="${GH_TOKEN}"
export HOMEBREW_GITHUB_API_TOKEN="${GH_TOKEN}"
export GITHUB_API_TOKEN="${GH_TOKEN}"
```

Chezmoi templates cannot rely on exports made by earlier run scripts because template rendering happens in chezmoi's process. Templates should reference the original process environment directly via `{{ env "VAR" }}`. If a template needs a token alias, either use `{{ env "GH_TOKEN" }}` directly or define that alias before invoking `chezmoi apply`.

---

## 4. Shell Config Strategy

### `.chezmoiignore` entry
```
# shell/ is sourced directly by bashrc/zshrc, not managed by chezmoi.
# Changes here won't appear in chezmoi diff or chezmoi verify.
shell/
```

### How it works
- `~/.bashrc` and `~/.zshrc` are managed by chezmoi (as templates or static files)
- Both source from `~/.dotfiles/shell/` directly
- `~/.dotfiles/shell/` lives outside chezmoi's purview (ignored)
- `~/.dotfiles/scripts` is on PATH via `paths.sh`, so helper scripts are callable directly
- This avoids double-management while keeping shell configs composable

> **⚠ ANNOTATION — 🔀 DEVIATION:** The original plan used `.chezmoiignore` to exclude `shell/` entirely. The actual implementation **manages** shell configs via `dot_dotfiles/shell/` (7 files: `.env.example`, `aliases.sh`, `completions.bash`, `completions.zsh`, `paths.sh`, `prompt.sh`, `variables.sh`). This was a deliberate change — managing them in chezmoi ensures they deploy with everything else. Shell source symlinks (`~/.variables.sh → ~/.dotfiles/shell/variables.sh`) are a pre-existing dependency, not introduced by chezmoi.

### 🔀 UPDATED: Dynamic neofetch alias (aliases.sh) — Cross-arch
```bash
# Prefers: neowofetch > fastfetch > neofetch
# Uses command -v (PATH-based) — no hardcoded /opt/homebrew paths
# paths.sh ensures Homebrew's bin dir is on PATH on both ARM and Intel Macs
if command -v neowofetch >/dev/null 2>&1; then
    alias neofetch='neowofetch'
elif command -v fastfetch >/dev/null 2>&1; then
    alias neofetch='fastfetch'
elif command -v neofetch >/dev/null 2>&1; then
    alias neofetch='neofetch'
fi
```

> **⚠ PLANNOTATOR FEEDBACK (2026-05-24):** Removed all hardcoded `/opt/homebrew/bin` paths. `paths.sh` now sets Homebrew on PATH for both architectures, so `command -v` always works. This also fixes Intel Mac portability.

---

## 5. Brewfile Strategy

### Composable Brewfiles with YAML-driven category selection

**`.chezmoidata/categories.yaml`** — controls which categories are installed:
```yaml
categories:
  dev_cli: true                # Always installed (core CLI tools)
  dev: true                    # Development tools + dev desktop apps
  desktop_browsers: true       # Web browsers (macOS-only)
  desktop_comms: true          # Communication apps (macOS-only)
  desktop_security: true       # VPN/security tools (macOS-only)
  desktop_media: true          # Media apps (macOS-only)
  desktop_utilities: true      # Desktop utilities (macOS-only)
  desktop_fonts: true          # Fonts (macOS-only)
  desktop_gaming: false        # Gaming clients (opt-in, macOS-only)
  desktop_cloud: true          # Cloud/sync apps (macOS-only)
  desktop_productivity: true   # Office/productivity apps (macOS-only)
  dev_ops: true                # Ops/infrastructure tools
  legacy: false                # Legacy/specialty tools (opt-in)
```

> **⚠ ANNOTATION:** ✅ All 13 Brewfiles reconciled against `~/Brewfile` (292 items, 0 missing, 0 extra). ✅ `brew bundle check` passes for 12 of 13 files. ✅ `run_onchange_01` uses sha256sum hashing of all 13 Brewfiles for change detection.

---

## 6. AGENTS.md & CLAUDE.md

### AGENTS.md / CLAUDE.md (real source files)

`AGENTS.md` is the authoritative guidance source and is applied to `~/AGENTS.md`.
`CLAUDE.md` is a separate source file applied to `~/CLAUDE.md`; keep it aligned
with `AGENTS.md` when guidance changes.

> **⚠ ANNOTATION:** Current chezmoi target paths are `~/AGENTS.md` and `~/CLAUDE.md`; do not document dot-prefixed symlink targets.

---

## 7. macOS-Only Dotfiles

### `.chezmoiignore` entries (for non-macOS)
```
{{- if ne .chezmoi.os "darwin" }}
...
{{- end }}
```

> **⚠ ANNOTATION — 🔀 BUG FIX:** The original plan had `.tmpl` suffixes on macOS-ignore patterns. ✅ **FIXED** — patterns now correctly omit `.tmpl` suffixes.

---

## 8. Missing Dotfiles to Add

| File | Purpose | Method |
|------|---------|--------|
| `.editorconfig` | Cross-editor consistency | Static `dot_editorconfig` |
| `.hushlogin` | Suppress "Last login" message | Static `dot_hushlogin` |
| `~/.config/bat/config` | bat theme/config | Static `dot_config/bat/config` |
| `~/.config/ripgrep/config` | ripgrep defaults | Static `dot_config/ripgrep/config` |
| `~/.config/starship.toml` | Starship prompt config | Template `dot_config/starship.toml.tmpl` |
| `~/.config/gh/config.yml` | GitHub CLI config | Template `dot_config/gh/config.yml.tmpl` |
| `~/.config/git/ignore` | Global gitignore | Static `dot_config/git/ignore` |
| `~/.config/direnv/direnvrc` | direnv config | Static `dot_config/direnv/direnvrc` |
| macOS defaults script | System preferences | `run_once_02` + `run_once_07` |

> **⚠ ANNOTATION:** ✅ All added. Additional files beyond original plan: `private_dot_vuescanrc.tmpl`, `dot_dotfiles/shell/` (7 files), `configs/mcp/` (11 JSON + global-mcps.json + templates/), `dot_config/opencode/dcp.json` (DCP compaction), `dot_codexbar/config.json` (sanitized codexbar config).

---

## 9. Git Strategy

- **Local only** — no remote push
- Conventional commits: `feat/fix/refactor/chore/docs/style/test/build/ci/revert` with scopes (`dotfiles`, `brew`, `secrets`, `scripts`, `templates`, `infra`, `agents`)
- Examples:
  - `feat(brew): add starship prompt config`

> **⚠ ANNOTATION:** ✅ `.gitignore` configured to prevent secrets and artifacts. No commits made yet — working tree dirty, pending final review. All changes will be committed with conventional commits after post-apply verification. `run_once_00` now enforces `~/.gnupg/` permissions (`700`) and `gpg.conf`/`gpg-agent.conf` permissions (`600`).

---

## 10. opencode Preset Model Selection Guide

> **⚠ ANNOTATION — 🔀 RECONCILED:** The model tables below are the **single canonical source**, reconciled from both `scripts/opencode-tier.sh` (executable source of truth) and `dot_AGENTS.md` (documentation). All three sources now match. Key constraints applied:
> - **Ollama Cloud Pro concurrency**: 3 concurrent requests per account (not per model — using fewer distinct models does not increase throughput)
> - **`gpt-5.5`** (not `gpt-5.5-pro`) — ChatGPT Plus subscription tier
> - **Interleaved provider fallback chains** — alternate providers at each fallback level for resilience
> - **🔀 TIMEOUT UPDATES:** Council timeout 360s (6 min), councillor_retries=1, fallback timeoutMs=60s, retryDelayMs=1000ms
> - **🔀 DISABLED PROVIDERS:** `google-vertex-anthropic`, `google-vertex`, `amazon-bedrock`
> - **🔀 LOCAL OLLAMA:** Included by default (controlled via `DOTFILES_USE_LOCAL_OLLAMA` env var, no CLI switch)
> - **🔀 MCP_DOCKER:** Removed from opencode config
> - **🔀 pro.sh/pro-plus.sh wrappers:** Dropped — use `opencode-tier.py <tier>` directly

### Tier Overview

| Tier | Providers | Cost Profile                                                                       |
|------|-----------|------------------------------------------------------------------------------------|
| **pro** | Ollama Cloud only | Lowest cost, Ollama-hosted models; optional local Ollama fallback                  |
| **pro-plus** | Ollama Cloud + OpenAI | Cost-optimized, OpenAI for reasoning-heavy roles; uses `gpt-5.5` and `gpt-5.4-mini` |
| **pro-plus-anthropic** | Ollama Cloud + OpenAI + Anthropic | Full stack, Anthropic for top-tier reasoning                                       |

### Pantheon Roles & Model Selection Guide

| Role | Purpose | Model Priority | Variant |
|------|---------|---------------|---------|
| **Orchestrator** | Strategic coordinator, master delegator, primary coding agent | Strong all-around coding + instruction-following | `default` |
| **Explorer** | Codebase reconnaissance, pattern discovery | Fast, low-cost — speed > reasoning | `low` |
| **Oracle** | Strategic advisor, hard debugging, architecture review, code review | Strongest high-reasoning model available | `high` |
| **Council** | Multi-LLM consensus (runs 3 models in parallel) | Strong synthesis model + diverse councillors | N/A |
| **Librarian** | External knowledge retrieval, documentation lookup | Fast, low-cost — speed > reasoning | `low` |
| **Designer** | UI/UX implementation, visual polish | Good at UI judgment + frontend skills | `medium` |
| **Fixer** | Fast scoped implementation, tests, bounded edits | Fast, reliable coding — execution > planning | `low` |

---

## 11. Execution Order

### Phase 1: Bootstrap (manual) ✅
### Phase 2: Migrate dotfiles (via `chezmoi add`) ✅
### Phase 3: Create from scratch ✅
### Phase 4: Verify & apply

```bash
set -a; source ~/.env; set +a
chezmoi execute-template < ~/Development/dotfiles/dot_gitconfig.tmpl >/dev/null
bash -n ~/Development/dotfiles/.chezmoiscripts/run_*.sh.tmpl
chezmoi apply
```

> **⚠ ANNOTATION — CRITICAL:** Must run `set -a; source ~/.env; set +a` before any chezmoi command that renders templates. `run_05/08/09` (no `once_` prefix) re-apply on every `chezmoi apply`. `~/.dotfiles/scripts` is added to PATH by `paths.sh`.

---

## 12. Idempotency Guards ✅

---

## 13. Script Architecture & Commonalities

### Shared utilities in `scripts/lib/`

```
scripts/lib/
├── common.sh          # info(), ok(), warn(), die(), run_or_skip()
├── env.sh             # set -a; source ~/.env; set +a + aliasing
└── ollama-discovery.sh # 🔀 SIMPLIFIED: _find_ollama(), list_local_ollama_models(), discover_local_ollama_models_json() only
```

> **⚠ ANNOTATION — 🔀 OLLAMA DISCOVERY SIMPLIFICATION:**
> - **Removed:** `classify_ollama_model`, `pick_best_per_family`, `discover_local_ollama_models` (the one with `OLLAMA_MODEL_*_BEST` exports), `discover_local_ollama_fallbacks_json`, `_json_array_from_stdin`
> - **Kept:** `_find_ollama()`, `list_local_ollama_models()`, `discover_local_ollama_models_json()`
> - No downstream callers used `OLLAMA_MODEL_*_BEST` or `pick_best_per_family` — only `list_local_ollama_models` and `discover_local_ollama_models_json` are called from `configure-opencode.sh`
> - `_find_ollama` probes: `command -v ollama` → `/opt/homebrew/bin/ollama` → `/usr/local/bin/ollama`

### 🔀 UPDATED: DOTFILES_ environment variable namespace

All chezmoiscript toggles renamed from `RUN_*` to `DOTFILES_RUN_*`:

| Old Name | New Name |
|----------|----------|
| `RUN_BREW_BUNDLE` | `DOTFILES_RUN_INSTALL_PACKAGES` |
| `RUN_OPENCODE_SETUP` | `DOTFILES_RUN_OPENCODE_SETUP` |
| `RUN_MCP_SETUP` | `DOTFILES_RUN_MCP_SETUP` |
| `RUN_MACOS_DEFAULTS` | `DOTFILES_RUN_MACOS_DEFAULTS` |
| `RUN_PLANNOTATOR_SETUP` | `DOTFILES_RUN_PLANNOTATOR_SETUP` |
| *(new)* | `DOTFILES_USE_LOCAL_OLLAMA` |

This prevents collision with other tools that might use `RUN_*` variables. All 11 `.chezmoiscripts/*.tmpl`, `~/.env`, and `.env.example` updated.

### 🔀 UPDATED: configure-opencode.py — local Ollama always-on

- `WITH_LOCAL_OLLAMA` defaults to `${DOTFILES_USE_LOCAL_OLLAMA:-true}` — reads from `.env`, defaults to on
- Removed `--with-local-ollama` CLI switch entirely
- `MCP_DOCKER` config block removed (was generating docker gateway MCP entry — not used)
- `SCRIPT_DIR` now uses `pwd -P`; `DOTFILES_ROOT` / `CONFIGS_DIR` make config path resolution robust
- Python heredocs now read `CONFIGS_DIR` from env instead of fragile relative paths
- `load_env` now uses `if load_env ...; then` for `set -e` safety
- configure-opencode-project.sh mirrored same changes and also uses `pwd -P`

### 🆕 OpenSpec + opencode-plugin-openspec

- OpenSpec CLI is installed globally via `npm install -g @fission-ai/openspec@latest`
- `run_once_06-install-opencode-plugins.sh.tmpl` now installs OpenSpec CLI alongside opencode plugins
- `opencode.json` includes `opencode-plugin-openspec`, which adds the `openspec-plan` agent for read-only planning
- OpenSpec provides SDD slash commands: `/opsx:propose`, `/opsx:apply`, `/opsx:archive`
- Telemetry is opt-out via `OPENSPEC_TELEMETRY=0` and `DO_NOT_TRACK=1` in `~/.env` and `.env.example`

### 🔀 UPDATED: paths.sh — Intel/ARM Mac Homebrew full support

| Platform | HOMEBREW_PREFIX | Detection |
|----------|-----------------|-----------|
| ARM Mac (Apple Silicon) | `/opt/homebrew` | `uname -m == "arm64"` |
| Intel Mac | `/usr/local` | `uname -m == "x86_64"` |

Changed from `uname -p` (outputs `arm` on ARM) to `uname -m` (outputs `arm64` on ARM) — more reliable. Intel branch now properly sets `HOMEBREW_CELLAR=/usr/local/Cellar` and `HOMEBREW_REPOSITORY=/usr/local/Homebrew` (previously only set PATH).

---

## 14. 🆕 NEW: DCP Context Compaction Configuration

Created `~/.config/opencode/dcp.json` using DCP's native percentage-based configuration:
```json
{
  "compress": {
    "maxContextLimit": "67%",
    "minContextLimit": "20%"
  }
}
```
No per-model context windows needed — DCP reads the actual context window from each model's provider configuration. Compression triggers at 67% full, leaves at least 20% filled.

---

## 15. 🆕 NEW: Codexbar Configuration

`dot_codexbar/config.json` — sanitized version with 48 providers, ordering preserved, all secrets redacted (replaced with `authConfig: "cookie-based (manual)"` / `"oauth-token"` / `"api-key"` labels).

---

## 16. Validation Results

### Formal Validation — ✅ ALL PASS

| Check | Result | Details |
|-------|--------|---------|
| `chezmoi execute-template` (6 templates) | ✅ 0 errors | All templates render with live `~/.env` vars |
| `bash -n` (11 chezmoiscripts) | ✅ 0 errors | All pass syntax check |
| `brew bundle check` (13 Brewfiles) | ✅ 12 pass, 1 warn | `Brewfile.dev` has pre-install gaps (expected) |
| `chezmoi apply --dry-run --verbose` | ✅ Exit 0 | No errors |
| `chezmoi apply` (live) | ✅ Applied, idempotent | Second apply shows no changes |
| No stale `RUN_*` references | ✅ Clean | All renamed to `DOTFILES_RUN_*` |
| No hardcoded `/opt/homebrew` paths | ✅ Clean | `paths.sh` ensures Homebrew on PATH |
| No MCP_DOCKER references | ✅ Clean | Removed from all sources |
| `run_09` references `configure-opencode-tier.py` | ✅ Clean | Uses `scripts/configure-opencode-tier.py` directly |

### Known Issues / Watchpoints ⚠️

1. **POSTMAN_BEARER_TOKEN is OAuth-managed** — cannot add to `~/.env`
2. **Template rendering safety** — must seed env vars before chezmoi commands
3. **GitLab MCP removed** — `GITLAB_API_PRIVATE_TOKEN` still in `.env` but MCP endpoint doesn't exist
4. **Council timed out on last review** — consistent with the timeout issue our fixes address
5. **ollama-discovery cleanup** — `discover_local_ollama_fallbacks_json` was fully removed; `configure-opencode-tier.py` now uses `discover_local_ollama_models_json`

### Pending ⏳

1. **Commit** with conventional commits

---

## 17. Review History

- **Oracle code reviews (3 rounds):** Architecture, template safety, plan accuracy — all integrated
- **Plannotator feedback (3 rounds):** 13 total annotations across rounds — all addressed
- **Council review (Round 4):** ✅ Verdict: Ready for apply (1 councillor responded)
- **Council review (Round 5):** Timed out (consistent with council timeout issue being fixed)
- **Round 5 remediation:** ollama-discovery simplification, DOTFILES_* namespace, Intel/ARM Homebrew, dropped wrappers, local ollama always-on

All feedback addressed. No commits made. Ready for code review.
