```plain text
eeeeee eeeee eeeee eeee e  e     eeee eeeee
8   8 8  88   8   8    8  8     8    8   "
8e  8 8   8   8e  8eee 8e 8e    8eee 8eeee
88  8 8   8   88  88   88 88    88      88
88ee8 8eee8   88  88   88 88eee 88ee 8ee88
```

[@randytarampi](https://github.com/randytarampi)'s [`dotfiles`](https://github.com/randytarampi/dotfiles) managed by [`chezmoi`](https://chezmoi.io/).

## Quick Start

```bash
# Preview pending changes with ~/.env loaded
make diff

# Run lint, env drift reporting, and a chezmoi dry run
make test

# Apply all dotfiles with ~/.env loaded
make deploy
```

## Initial Installation & Run Instructions

### Prerequisites
- **macOS / Linux:** Homebrew (`brew`)
- **Windows:** PowerShell 7 (`pwsh`), Windows Package Manager (`winget`)

---

### Step 1: Clone the Repository
Clone the repository to your local development directory (by default, `~/Development/dotfiles` or `$HOME\Development\dotfiles`):
```bash
mkdir -p ~/Development
git clone https://github.com/<username>/dotfiles.git ~/Development/dotfiles
cd ~/Development/dotfiles
```

---

### Step 2: Seed the Local Environment (`.env`)
Local configuration toggles and secrets are managed through `~/.env` (on Windows, `$HOME\.env` or `%USERPROFILE%\.env`):
1. Copy the canonical template to your home directory:
   - **macOS / Linux:**
     ```bash
     cp dot_dotfiles/shell/.env.example ~/.env
     ```
   - **Windows (PowerShell):**
     ```powershell
     copy dot_dotfiles\shell\.env.example $HOME\.env
     ```
2. Open the file in your preferred editor and populate your API keys (such as `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`) and toggle gates. To enable automatic package installation during setup, ensure:
   ```env
   DOTFILES_RUN_INSTALL_PACKAGES=1
   ```

---

### Step 3: Initialize and Apply via Makefile + chezmoi
We use `chezmoi` to manage all symlinks, templates, and setup run scripts. The Makefile is the canonical local entrypoint because it loads `~/.env` in the same shell process as each chezmoi command:
- **macOS / Linux:**
  ```bash
  # Ensure chezmoi is installed
  command -v chezmoi >/dev/null 2>&1 || brew install chezmoi

  # Initialize source, preview, then apply
  chezmoi init --source ~/Development/dotfiles
  make diff
  make deploy
  ```
- **Windows (PowerShell 7):**
  ```powershell
  # Ensure chezmoi is installed
  if (-not (Get-Command chezmoi -ErrorAction SilentlyContinue)) {
      winget install twpayne.chezmoi
  }

  # Initialize source and apply templates
  chezmoi init --source "$HOME\Development\dotfiles"
  make deploy
  ```

---

### Step 4: Setup Offline/Local Linting Tools & Verification (Optional)
To verify files and run offline/local check suites using the project's central `Makefile` and `pre-commit` hook setup:
- **macOS / Linux:**
  ```bash
  # Install linting & formatting packages from Brewfile.dev (black, shellcheck, shfmt, pre-commit)
  brew bundle --file Brewfile.dev

  # Install git pre-commit hooks in the repository
  pre-commit install

  # Run standard offline code verification checks
  make test
  # Or via pre-commit:
  pre-commit run --all-files
  ```
- **Windows (PowerShell 7 / Git Bash):**
  Windows users can run these checks inside POSIX-compatible environments such as Git Bash, WSL, or MSYS2, or natively:
  ```powershell
  # Ensure all CLI linting packages are installed via winget
  # (Automatic if DOTFILES_RUN_INSTALL_PACKAGES=1 was enabled in ~/.env on chezmoi apply)
  # Or install manually via winget:
  winget install GnuWin32.Make OpenJS.NodeJS Python.Python.3.12 psf.black koalaman.shellcheck mvdan.shfmt

  # Install pre-commit using Python pip
  pip install pre-commit
  pre-commit install

  # Run verification checks natively or inside Git Bash / WSL for Unix utility compatibility
  pre-commit run --all-files
  ```

## Commands

```bash
make diff               # Preview pending changes with ~/.env loaded
make dry-run            # Dry-run apply with ~/.env loaded
make deploy             # Apply all dotfiles with ~/.env loaded
make test               # Lint, env drift report, and dry run
make env-check          # Report ~/.env drift from dot_dotfiles/shell/.env.example
make env-sync           # Append missing template keys to ~/.env as comments
chezmoi edit ~/.bashrc  # Edit a managed dotfile
scripts/configure-opencode-tier.py pro-plus   # Switch AI model tier
scripts/configure-mcp-all.py                  # Regenerate MCP configs
scripts/configure-opencode.py       # Regenerate OpenCode config
```

## Conventions

- **Commits:** `feat/fix/refactor/chore/docs` with scopes: `dotfiles`, `brew`, `secrets`, `scripts`, `templates`, `infra`, `agents`
- **Shell scripts:** `#!/usr/bin/env bash`, `set -euo pipefail`, source `lib/common.sh`
- **Templates:** `{{ env "VAR" }}` syntax, `private_` prefix for 600 perms
- **Run scripts:** `run_once_*` idempotent, `run_*` re-runs every apply, `run_onchange_*` re-runs on content change
- **Env vars:** `DOTFILES_` prefix for dotfiles-system toggles, `DOTFILES_RUN_*` for script gates
- **Secrets:** Never committed. `~/.env` is single source of truth. No age/GPG encryption (local repo).
- **Shell indent:** 2-space (not tabs), enforced by `.editorconfig` + `shfmt`
- **Lint/format:** `shellcheck`, `shfmt`, `pre-commit` (runs local/offline `make lint`), `black`, JSON/YAML/Large files validation via `Makefile`
- **Cross-platform:** `brew --prefix` pattern for all Homebrew paths (Intel/ARM agnostic). Network failures in `chezmoi apply` scripts warn, never abort.

## Toggles

Set in `~/.env` (0 = skip, 1 = run):

| Toggle | Purpose | Default |
|--------|---------|---------|
| `DOTFILES_RUN_INSTALL_PACKAGES` | Homebrew + winget package installs | 0 |
| `DOTFILES_RUN_MCP_SETUP` | MCP config deployment | 0 |
| `DOTFILES_RUN_OPENCODE_SETUP` | Secrets + OpenCode tier config | 1 |
| `DOTFILES_RUN_MACOS_DEFAULTS` | macOS system preferences | 0 |
| `DOTFILES_RUN_MACOS_SECURITY` | macOS security defaults (firewall, FileVault, etc.) | 0 |
| `DOTFILES_RUN_MERIDIAN_LAUNCHD` | Meridian launchd plist | 0 |
| `DOTFILES_RUN_PLANNOTATOR_SETUP` | Plannotator install/update | 1 |
| `DOTFILES_RUN_JUNIE_CLI_SETUP` | Junie CLI EAP install | 1 |
| `DOTFILES_USE_LOCAL_OLLAMA` | Include local Ollama in OpenCode | 1 |
| `OPENSPEC_TELEMETRY` | OpenSpec telemetry opt-out | 0 |
| `DO_NOT_TRACK` | Global telemetry opt-out | 1 |

## Structure

```
~/Development/dotfiles/           # chezmoi source directory
├── .chezmoi.toml.tmpl            # chezmoi config (sourceDir, minimumVersion)
├── .chezmoiignore                # ignore patterns (scripts/, configs/, macOS-only)
├── .chezmoidata/
│   └── categories.yaml           # Brewfile + wingetfile category toggles
├── .chezmoiscripts/              # Run scripts (16 total, ordered: defaults → packages → config)
│   ├── # Phase 1: System defaults
│   ├── run_once_01-setup-chezmoi.sh.tmpl            # env aliasing, directories, symlinks
│   ├── run_once_02-configure-macos-defaults.sh.tmpl   # macOS system prefs
│   ├── run_once_03-configure-mac-defaults-extended.sh.tmpl  # Extended macOS prefs
│   ├── run_once_04-configure-macos-security.sh.tmpl  # macOS security defaults
│   ├── run_once_05-configure-git.sh.tmpl            # git config, GPG signing
│   ├── run_once_06-configure-ssh.sh.tmpl            # SSH key permissions
│   ├── run_once_07-configure-iterm2.sh.tmpl         # iTerm2 shell integration
│   ├── # Phase 2: Package installation
│   ├── run_onchange_08-install-packages.sh.tmpl      # brew + winget (re-runs on Brewfile/wingetfile change)
│   ├── run_once_09-install-junie-cli.sh.tmpl         # Junie CLI EAP install/update
│   ├── run_once_10-install-opencode-plugins.sh.tmpl # opencode plugins + OpenSpec
│   ├── run_once_11-install-plannotator.sh.tmpl       # Plannotator install/update
│   ├── # Phase 3: Tool configuration
│   ├── run_onchange_12-configure-secrets.sh.tmpl    # AI tool .env files (every apply)
│   ├── run_once_13-configure-mcp.sh.tmpl             # MCP configs (one-time setup, gated by DOTFILES_RUN_MCP_SETUP)
│   ├── run_once_14-configure-opencode.sh.tmpl        # Tier config (every apply)
│   ├── run_once_15-configure-mozart-router.sh.tmpl  # Mozart router setup
│   └── run_once_16-install-meridian-launchd.sh.tmpl # Meridian launchd plist
├── AGENTS.md                  # AI agent guidance (authoritative — scripts, tiers, MCP, symlinks)
├── dot_bashrc                     # Bash config
├── dot_zshrc                      # Zsh config
├── dot_emacs                      # Emacs config (agent-shell + ACP)
├── dot_gitconfig.tmpl            # Git identity + settings (env var-driven)
├── dot_gitignore                 # Global gitignore
├── dot_nvmrc                     # Default Node.js version (24)
├── dot_config/
│   ├── bat/config                # bat theme/config
│   ├── direnv/direnvrc           # direnv config
│   ├── gh/config.yml.tmpl        # GitHub CLI config
│   ├── git/ignore                # Global gitignore rules
│   ├── opencode/
│   │   └── dcp.json              # DCP context compaction (percentage thresholds)
│   ├── ripgrep/config            # ripgrep defaults
│   └── starship.toml.tmpl        # Starship prompt
├── dot_dotfiles/                 # Managed shell configs (→ ~/.dotfiles/shell/)
│   └── shell/
│       ├── .env.example           # Canonical template for ~/.env
│       ├── acme.sh                # acme.sh env sourcing
│       ├── aliases.sh             # Shell aliases (dynamic neofetch)
│       ├── bun.sh                 # Bun env + completions
│       ├── completions.bash       # Bash/zsh-aware completions
│       ├── completions.zsh        # → completions.bash
│       ├── paths.sh               # PATH setup (brew --prefix first, Intel/ARM fallback)
│       ├── prompt.sh              # Shell prompt (starship with fallback)
│       └── variables.sh           # Non-secret env vars
├── dot_mozart/mozart.json.tmpl    # Mozart router config (Ollama Cloud + Meridian gateways, env-var-driven)
├── private_dot_aws/credentials.tmpl        # AWS credentials (2 profiles)
├── private_dot_gnupg/
│   ├── gpg.conf.tmpl              # GPG config (default-key from env)
│   └── gpg-agent.conf             # GPG agent config
├── private_dot_npmrc.tmpl        # npm auth tokens
├── private_dot_ssh/config        # SSH config
├── private_dot_vuescanrc.tmpl    # VueScan license
├── CLAUDE.md                     # Applied to ~/CLAUDE.md; keep aligned with AGENTS.md
├── Brewfile*                     # 13 composable Brewfiles (macOS/Linux)
├── wingetfile*                   # Category-based wingetfiles (Windows)
├── configs/
│   ├── junie/model-groups.json   # Junie model profile definitions
│   ├── mcp/                      # MCP server configs
│   │   ├── betterstack.json
│   │   ├── github.json
│   │   ├── global-mcps.json       # Tool→template registry
│   │   ├── idea.json              # JetBrains MCP — SSE transport (stdio via IJ_MCP_TRANSPORT=stdio)
│   │   ├── mongodb.json
│   │   ├── notion.json
│   │   ├── sentry.json            # Passes SENTRY_ACCESS_TOKEN via env, not CLI args
│   │   ├── shortcut.json
│   │   └── templates/            # Symlinks → ../ for configure-mcp-tool.sh
│   ├── iterm2/Default.json        # iTerm2 Dynamic Profile (clean, no secrets)
│   ├── mozart-router/mozart.json # Mozart AI router gateway config
│   └── opencode/
│       ├── oh-my-opencode-slim.json  # Presets, council, fallbacks, tier overrides
│       ├── anthropic-models.json     # Relocated
│       ├── role-to-local-category.json # New
│       ├── openai-models.json        # New
│       └── ollama-cloud-models.json  # New
├── ~/.dotfiles/scripts -> ~/Development/dotfiles/scripts  # helper-script symlink on PATH
└── scripts/                      # Utility scripts + lib/
    ├── lib/                       # Shared helpers
    │   ├── common.sh              # Standardized logging & ERR trap stack traces
    │   ├── env.sh                 # load_env(), alias_github_token() for shell bootstrap
    │   ├── logger.py              # Central Python logging module
    │   ├── env.py                 # load_env() and token aliases in Python
    │   ├── ai_mcps.py             # Filter template globs in Python
    │   ├── ai_dirs.py             # Python-ported platform-independent directory setups
    │   ├── ai_models.py           # Python-ported model prefix mappings & temperatures
    │   ├── idea.py                # Resolve IntelliJ app paths, java, and MCP classpaths in Python
    │   └── discover_models.py     # Local Ollama discovery to JSON in Python
    ├── configure-mcp-all.py       # Generate MCP configs for all AI tools
    ├── configure-jetbrains-ai.py  # JetBrains AI: models, dirs, symlinks, MCP
    ├── configure-opencode-project.py # Write project-specific OpenCode config overrides
    ├── configure-mozart-router.py # Configure Mozart AI router
    ├── configure-ai.py            # Resolve paths/secrets for AI tool .env files
    ├── configure-jetbrains-workspace.py # Configure AI dirs in JB workspace modules
    ├── verify-brewfile-completeness.py # Verify Brewfile completeness
    ├── detect-ij-mcp.py           # Detect JetBrains MCP server paths (SSE default)
    ├── configure-mcp-tool.py      # Generate MCP config for a single tool
    ├── configure-meridian.py      # Add Meridian proxy to OpenCode config
    ├── configure-opencode.py      # Write OpenCode config (local ollama default)
    ├── configure-opencode-tier.py # Switch active preset tier (source of truth)
    ├── generate-jetbrains-profiles.py # Generate model profiles JSON files
    ├── get-tools.py               # Get MCP tool registry keys
    ├── install-opencode.sh        # Install OpenCode plugins and tools
    ├── install-nvm-lts.sh         # Reinstall all LTS node versions
    └── meridian-launch.sh         # Launch wrapper for meridian (Keychain-aware)
```

## Secrets Management

All secrets live in `~/.env` using `KEY='VALUE'` format. Templates use `{{ env "VAR" }}` syntax.

**`.env.example`** in the repo documents all available keys. Run `make env-check` to report drift and `make env-sync` to append newly documented keys to `~/.env` as commented examples without overwriting secrets.

**Load env manually only for ad hoc raw chezmoi commands:** `set -a; source ~/.env; set +a`. Prefer `make diff`, `make dry-run`, and `make deploy` for normal work.

### Migrating old `~/.credentials.sh` files

Old sourced credentials files used shell exports such as `export GH_TOKEN=...`. Convert them into `~/.env` assignments without `export`:

| Old sourced-file pattern | New `~/.env` entry |
|--------------------------|--------------------|
| `export GH_TOKEN=...` or `export GITHUB_TOKEN=...` | `GH_TOKEN='...'` |
| `export OPENAI_API_KEY=...` | `OPENAI_API_KEY='...'` |
| `export ANTHROPIC_API_KEY=...` | `ANTHROPIC_API_KEY='...'` |
| `export AWS_ACCESS_KEY_ID=...` | `AWS_ACCESS_KEY_ID='...'` |
| `export AWS_SECRET_ACCESS_KEY=...` | `AWS_SECRET_ACCESS_KEY='...'` |
| `export NPM_TOKEN=...` | `NPM_TOKEN='...'` |
| `export SENTRY_ACCESS_TOKEN=...` | `SENTRY_AUTH_TOKEN='...'` |

`GH_TOKEN` is canonical for GitHub. `SENTRY_AUTH_TOKEN` is canonical for the user env file; MCP generation maps it into the `SENTRY_ACCESS_TOKEN` environment expected by the Sentry MCP server.

**Key template files:**
- `dot_gitconfig.tmpl` — `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`, `GPG_SIGNING_KEY`, `GITHUB_USER`, `GH_TOKEN`
- `private_dot_npmrc.tmpl` — `NPM_TOKEN`, `GH_TOKEN`
- `private_dot_aws/credentials.tmpl` — `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`
- `private_dot_vuescanrc.tmpl` — `VUESCAN_USER_ID`, `VUESCAN_LICENSE`, `VUESCAN_CUSTOMER`, `VUESCAN_EMAIL`
- `private_dot_gnupg/gpg.conf.tmpl` — `GPG_SIGNING_KEY`

---

## Package Management

### Brewfiles (macOS/Linux)

13 composable Brewfiles controlled by `.chezmoidata/categories.yaml`:

| Category | File | Default | Scope |
|----------|------|---------|-------|
| dev.cli | `Brewfile` | on | Core CLI tools |
| dev | `Brewfile.dev` | on | Languages, libraries, dev apps |
| desktop.browsers | `Brewfile.desktop.browsers` | on | macOS-only |
| desktop.comms | `Brewfile.desktop.comms` | on | macOS-only |
| desktop.security | `Brewfile.desktop.security` | on | macOS-only |
| desktop.media | `Brewfile.desktop.media` | on | macOS-only |
| desktop.utilities | `Brewfile.desktop.utilities` | on | macOS-only |
| desktop.fonts | `Brewfile.desktop.fonts` | on | macOS-only |
| desktop.gaming | `Brewfile.desktop.gaming` | off | Opt-in, macOS-only |
| desktop.cloud | `Brewfile.desktop.cloud` | on | macOS-only |
| desktop.productivity | `Brewfile.desktop.productivity` | on | macOS-only |
| dev.ops | `Brewfile.dev.ops` | on | Infrastructure tools |
| legacy | `Brewfile.legacy` | off | Opt-in |

Homebrew is installed automatically if missing (via `run_onchange_08-install-packages.sh.tmpl`). On Linux, Homebrew installs to `/home/linuxbrew/.linuxbrew` and `brew shellenv` is sourced automatically. Only `brew` entries run on Linux; `cask` entries are macOS-only and skipped.

Toggle a category in `categories.yaml`, then `make deploy` re-runs `brew bundle` when Brewfiles change.

### Wingetfiles (Windows)

Category-based wingetfiles mirror the Brewfile structure for Windows:

| Category | File | Default |
|----------|------|---------|
| winget | `wingetfile` | on |
| winget.dev | `wingetfile.dev` | on |
| winget.dev.ops | `wingetfile.dev.ops` | on |
| winget.desktop.browsers | `wingetfile.desktop.browsers` | on |
| winget.desktop.comms | `wingetfile.desktop.comms` | on |
| winget.desktop.security | `wingetfile.desktop.security` | on |
| winget.desktop.media | `wingetfile.desktop.media` | on |
| winget.desktop.utilities | `wingetfile.desktop.utilities` | on |
| winget.desktop.cloud | `wingetfile.desktop.cloud` | on |
| winget.desktop.productivity | `wingetfile.desktop.productivity` | on |

Cross-reference comments (e.g., `# Cross-ref: brew 'git'`) link winget packages to their brew equivalents.

### Cross-Platform Package Matrix

Packages available on both platforms by category:

| Package | brew (macOS/Linux) | winget (Windows) |
|---------|-------------------|------------------|
| Git | `git` | `Git.Git` |
| Starship | `starship` | `Starship.Starship` |
| ripgrep | `ripgrep` | `BurntSushi.ripgrep.MSVC` |
| fastfetch | `fastfetch` | `Fastfetch-cli.Fastfetch` |
| AWS CLI | `awscli` | `Amazon.AWSCLI` |
| Session Manager | `session-manager-plugin` | `Amazon.SessionManagerPlugin` |
| Docker | `docker-desktop` (cask) | `Docker.DockerCLI` |
| Ollama | `ollama-app` (cask) | `Ollama.Ollama` |
| OpenCode | `opencode-desktop` (cask) / `anomalyco/tap/opencode` | `SST.OpenCodeDesktop` / `SST.opencode` |
| Codex | `codex` (cask) | `OpenAI.Codex` |
| VS Code | `visual-studio-code` (cask) | `Microsoft.VisualStudioCode` |
| Slack | `slack` (cask) | `SlackTechnologies.Slack` |
| Discord | `discord` (cask) | `Discord.Discord` |
| Firefox | `firefox@developer-edition` (cask) | `Mozilla.Firefox.en-CA` |
| Edge | `microsoft-edge@canary` (cask) | `Microsoft.Edge` |
| VLC | `vlc` (cask) | `VideoLAN.VLC` |
| Mullvad VPN | `mullvad-vpn` (cask) | `MullvadVPN.MullvadVPN` |
| PIA | `private-internet-access` (cask) | `PrivateInternetAccess.PrivateInternetAccess` |
| AWS VPN | `aws-vpn-client` (cask) | `Amazon.AWSVPNClient` |
| 1Password | `1password` (cask) | `AgileBits.1Password` |
| Sublime Text | `sublime-text` (cask) | `SublimeHQ.SublimeText.4` |
| Geekbench | `geekbench` (cask) | `PrimateLabs.Geekbench.5` / `PrimateLabs.Geekbench.6` |
| Steam | `steam` (cask) | `Valve.Steam` |

---

## AI

### Model Tiers

Seven presets for AI agents, defined in `scripts/configure-opencode-tier.py` (source of truth) and documented in `AGENTS.md`:

| Tier | Providers | Best For |
|------|-----------|----------|
| **pro** | Ollama Cloud | Daily coding, budget mode |
| **pro-plus** | Ollama Cloud + OpenAI (`gpt-5.5`) | General development |
| **pro-plus-anthropic** | Anthropic + Ollama Cloud + OpenAI | Heavy orchestration |
| **plus** | OpenAI only (`gpt-5.5`, `gpt-5.4-mini`) | OpenAI-first workflow |
| **anthropic** | Anthropic only (`opus-4-7`, `sonnet-4-6`, `haiku-4-5`) | Anthropic-first workflow |
| **local** | Local Ollama only | Fully offline/air-gapped |

Cloud presets (pro, pro-plus, pro-plus-anthropic) use Ollama Cloud models (e.g. `glm-5.1`, `kimi-k2.6`, `deepseek-v4-pro`). The `plus` preset uses OpenAI models exclusively. The `anthropic` preset uses Anthropic models exclusively. The `local` preset uses `_local:<category>` placeholders resolved at runtime by `configure-opencode-tier.py`.

**Variant policy:** oracle/council roles use `max` or `xhigh` (for models whose default is already high, like opus-4-7). Orchestrator gets no variant (default). Lightweight roles (librarian, explorer, observer) use `low`. Designer uses `medium`. Fixer uses `high` (code-specialized) or `low` (general). See `AGENTS.md` for the full variant convention table.

Switch tier: `scripts/configure-opencode-tier.py <tier>` (pro, pro-plus, pro-plus-anthropic, plus, anthropic, local)

Default preset: tier auto-detected from available API keys during `run_once_14-configure-opencode`. Auto-detection order: both keys → pro-plus-anthropic, Anthropic only → anthropic, OpenAI only → plus, no keys but Ollama → local, nothing → pro.

Local Ollama fallbacks are appended by default (use `--no-local-fallbacks` to omit). Fallbacks append **role-appropriate** local models per agent: reasoning models to oracle, code-gen models to orchestrator/fixer/designer, lightweight models to librarian/explorer, vision-capable models to observer. Classification uses name heuristics (r1/think/qwq → reasoning, coder/code/devstral → code-gen, mini/phi/smol → lightweight) with size-aware rules: models < 12 GB → lightweight, `ollama show` parameter-based classification for unclassified models (≥ 7B → reasoning, < 7B → code-gen), capability filtering (reasoning requires thinking+tools, code-gen requires thinking+completion, lightweight requires tools, vision requires tools+vision), and reasoning model reuse when no code-gen name-heuristic match is found. Override per-role: `--local-fallback-role observer=ollama/qwen3.5:9b-mlx`.

### DCP Context Compaction

`~/.config/opencode/dcp.json` uses percentage-based thresholds:
- Compress at **67%** of context window
- Leave at least **20%** filled

No per-model config needed — the plugin reads context windows from provider configs.

### Mozart Router

Local AI gateway router installed via `run_once_15-configure-mozart-router.sh.tmpl`.

- **Install:** `npm install -g mozart-router`
- **Configure:** `scripts/configure-mozart-router.py` writes `~/.mozart/mozart.json`
- **Gateways:** Ollama Cloud, OpenAI, and Anthropic Meridian — all via GenericOpenAI adapter
- **Meridian proxy:** Uses `MERIDIAN_HOST` and `MERIDIAN_PORT` env vars (defaults: `127.0.0.1` and `3456`) for the local Meridian endpoint
- **Usage:** `mozart-router doctor`, `mozart-router route "task description"`, `mozart-router proxy --port=4445`
- **MCP:** `mozart-router mcp` (stdio integration)
- Gated by `DOTFILES_RUN_MCP_SETUP`

### Plannotator

Installed via `run_once_11-install-plannotator.sh.tmpl`. Uses `curl -fsSL https://plannotator.ai/install.sh | bash` (idempotent). OpenCode plugin (`@plannotator/opencode@latest`) is already configured in global `opencode.json`. Use `/plannotator-review`, `/plannotator-annotate`, `/plannotator-last` in OpenCode.

### OpenSpec

Spec-driven development (SDD) for AI coding assistants.

- Install: `npm install -g @fission-ai/openspec@latest`
- Init: `cd your-project && openspec init`
- Commands: `/opsx:propose`, `/opsx:apply`, `/opsx:archive`
- Plugin: `opencode-plugin-openspec` adds the `openspec-plan` agent for read-only planning
- Telemetry: `OPENSPEC_TELEMETRY=0` and `DO_NOT_TRACK=1`

### Junie CLI

JetBrains EAP CLI installed via `run_once_09-install-junie-cli.sh.tmpl`. Cross-platform: `curl|bash` on Mac/Linux, PowerShell on Windows.

#### Junie Model Profiles

Generated dynamically by `scripts/configure-jetbrains-ai.py --models` from `configs/junie/model-groups.json`:

| Profile | Provider | Primary | Faster | Temp |
|---------|----------|---------|--------|------|
| `deepseek` | cloud | `deepseek-v4-pro` | `deepseek-v4-flash` | 0 |
| `glm` | cloud | `glm-5.1` | — | 0.7 |
| `kimi` | cloud | `kimi-k2.6` | — | 1 |
| `mistral` | cloud | `mistral-large-3:675b` | `ministral-3:14b` | 0.7 |
| `nemotron` | cloud | `nemotron-3-super` | `nemotron-3-nano:30b` | 0.7 |
| `qwen` | cloud | `qwen3.5:397b` | — | 0.6 |
| `gpt-oss` | cloud | `gpt-oss:120b` | `gpt-oss:20b` | 0.7 |
| `devstral` | cloud | `devstral-2:123b-cloud` | `devstral-small-2:24b` | 0.7 |
| `qwen3` | local | `qwen3.6:27b-mlx` | `qwen3.5:9b-mlx` | 0.6 |
| `qwen3-coder` | local | `qwen3.6:27b-coding` | `qwen3.5:9b-mlx` | 0.6 |
| `gemma4` | local | `gemma4:26b-mlx` | `gemma4:e4b-mlx` | 0.7 |

Local Ollama profiles resolve model IDs dynamically via `ollama ls` prefix matching. Cloud profiles use hardcoded IDs from `model-groups.json`. Temperatures follow Junie's recommendations.

Homebrew-aware scripts now prefer `brew --prefix` so they stay Intel/ARM agnostic.

Select via: `junie --model custom:<profile>`

### MCP Configuration

Centralized in `configs/mcp/`. `global-mcps.json` maps 7 AI tools to MCP templates. `configure-mcp-all.py` generates per-tool config files.

| Tool | Config Path | Format |
|------|------------|--------|
| OpenCode | `~/.config/opencode/opencode.json` | JSON internal (global: github, idea, sentry) |
| JetBrains AI | `~/.ai/mcp/mcp.json` | JSON mcpServers (global: github, idea, sentry) |
| Junie | `~/.ai/mcp/mcp.json` | JSON mcpServers (global: github, idea, sentry, via `.junie → .ai` symlink) |
| Air | `~/.ai/mcp/mcp.json` | JSON mcpServers (global: github, idea, sentry, shares `.ai` path) |
| Cursor | `~/.cursor/mcp.json` | JSON mcpServers (global: github, idea, sentry) |
| Codex | `~/.codex/config.toml` | TOML (global: github, idea, sentry) |
| Gemini | `~/.gemini/settings.json` | JSON merge (global: github, sentry) |

Global MCP servers: github, idea, sentry. Project-level MCP servers (betterstack, mongodb, shortcut, notion) are configured per-project via `configure-mcp-tool.py --mode project`.

`idea.json` uses SSE transport by default. Set `IJ_MCP_TRANSPORT=stdio` and run `detect-ij-mcp.py` for stdio mode. `run_once_13-configure-mcp.sh.tmpl` sources its output before the gate check.

---

## Shell & Editor

### Prompt

`dot_dotfiles/shell/prompt.sh` checks for starship first (`eval "$(starship init bash)"`), falling back to a debian_chroot-style prompt. `dot_config/starship.toml.tmpl` mirrors the fallback layout: `user@host path git_info ❯`.

### Emacs

`dot_emacs` configures [agent-shell](https://github.com/xenodium/agent-shell) with ACP support for OpenCode, Claude Agent, Codex, and Gemini CLI. Includes `claude-agent-acp` as a system dependency. Inherits environment from the parent Emacs process so API keys from `~/.env` are available.

### NVM

Default Node.js version: 24 (via `.nvmrc`). Reinstall all LTS versions: `scripts/install-nvm-lts.sh`

### pyenv

`dot_dotfiles/shell/paths.sh` includes a stale lock cleanup before `pyenv init` to prevent "cannot rehash: couldn't acquire lock" errors on startup.

---

## Platform Notes

### macOS

- Homebrew: ARM Mac prefix `/opt/homebrew`, Intel Mac `/usr/local`, auto-detected via `brew --prefix`
- Desktop apps (casks) are macOS-only, skipped on Linux
- iTerm2 config uses Dynamic Profiles JSON (no secrets, no plist)
- macOS security defaults gated by `DOTFILES_RUN_MACOS_SECURITY=1`
- Meridian launchd gated by `DOTFILES_RUN_MERIDIAN_LAUNCHD=1`

### Linux

- Homebrew installs to `/home/linuxbrew/.linuxbrew`, sourced via `brew shellenv`
- Only `brew` formulae install; `cask` entries are skipped
- Use `$(brew --prefix)` for Homebrew path resolution — no hardcoded platform paths

### Windows

- Package management via `winget` and `wingetfile*` category bundles
- `run_onchange_08-install-packages.sh.tmpl` handles both `brew bundle` and `winget install` (Windows-only section)
- Windows winget install is gated by the same `DOTFILES_RUN_INSTALL_PACKAGES` toggle as Homebrew
