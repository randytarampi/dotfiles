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
# Apply all dotfiles and run runtime config generation
make deploy
```

On first setup, run `make deploy` twice. The second pass should be a no-op, but it helps surface any idempotency gaps.

## Architecture

- Canonical orchestration doc: [docs/ORCHESTRATION.md](docs/ORCHESTRATION.md)
- Repo-level agent guidance: [AGENTS.md](AGENTS.md)
- Home-level agent guidance source: [configs/agents/home-agents.md](configs/agents/home-agents.md)

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
   DOTFILES_RUN_PACKAGES_SETUP=1
   ```

---

### Step 3: Initialize and Apply via Makefile + chezmoi
We use `chezmoi` to manage all symlinks, templates, and setup run scripts. The Makefile is the canonical local entrypoint because it loads `~/.env` in the same shell process as each chezmoi command:
- **macOS / Linux:**
  ```bash
  # Ensure chezmoi is installed
  command -v chezmoi >/dev/null 2>&1 || brew install chezmoi

  # Initialize source, then apply
  chezmoi init --source ~/Development/dotfiles
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
  make verify
  # Or via pre-commit:
  pre-commit run --all-files
  ```
- **Windows (PowerShell 7 / Git Bash):**
  Windows users can run these checks inside POSIX-compatible environments such as Git Bash, WSL, or MSYS2, or natively:
  ```powershell
  # Ensure all CLI linting packages are installed via winget
  # (Automatic if DOTFILES_RUN_PACKAGES_SETUP=1 was enabled in ~/.env on chezmoi apply)
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
make deploy             # Apply all dotfiles and regenerate runtime configs
make configure          # Rebuild runtime configs only
make dry-run            # Dry-run apply with ~/.env loaded
make diff               # Preview pending changes with ~/.env loaded
make doctor             # Read-only drift checks
make verify             # Lint, env drift report, doctor, hashes, and dry run
make check-hashes       # Hash trigger coverage audit
make reset        # Clear chezmoi script state
make drift               # Report ~/.env drift from dot_dotfiles/shell/.env.example
make migrate             # Migrate deprecated gate names + append missing template keys to ~/.env
chezmoi edit ~/.bashrc  # Edit a managed dotfile
scripts/configure-opencode-tier.py pro-plus   # Switch AI model tier
scripts/configure-opencode-voice.py --preset <tier>  # Configure voice plugin (tui.json)
scripts/configure-mcps.py                  # Regenerate MCP configs
scripts/configure-opencode.py       # Regenerate OpenCode config
scripts/configure-acp-agents.py --preset <tier>  # Regenerate ACP agent config
scripts/configure-smallcode.py --preset <tier>   # Configure SmallCode (env + TOML + MCP)
scripts/configure-smallcode.py --preset <tier> --no-local-fallbacks  # Without local models
scripts/install-smallcode.sh                     # Install SmallCode CLI
scripts/configure-skills.py                  # Distribute skills to all agent directories
make opencode-restart                        # Restart OpenCode Web service
make opencode-stop                           # Stop OpenCode Web service
make opencode-start                          # Start OpenCode Web service
make plannotator-restart                     # Clear Plannotator port conflicts
make services-restart                        # Restart all managed services
make ci-verify                               # CI verification (lint + drift + doctor + check-hashes, no dry-run)
```

### ACP Agents

`acpAgents` in `oh-my-opencode-slim.json` auto-exposes ACP-capable tools as sandboxed wrapper subagents.

- Auto-detected agents: `opencode`, `gemini`, `claude-code`, `codex`, `junie`, `cursor`, `cline`, `copilot`.
- `scripts/configure-opencode.py` runs `scripts/configure-acp-agents.py` during OpenCode config generation, gated by `DOTFILES_RUN_OPENCODE_SETUP=1`, and only emits entries for binaries found on `PATH`.
- `opencode` is included only when the `opencode` binary is on `PATH`, which enables recursive delegation via ACP.
- Install adapter prerequisites with `scripts/install-opencode.sh` (`brew install copilot-cli`, `npm i -g @zed-industries/claude-code-acp`, `npm i -g codex-acp`), then sign in to each agent separately.
- After the first install, run `make brewfile-sync` to capture the new npm globals in the Brewfiles.
- Regenerate with `scripts/configure-acp-agents.py --preset <tier>`.

## Conventions

- **Commits:** `feat/fix/refactor/chore/docs` with scopes: `dotfiles`, `brew`, `secrets`, `scripts`, `templates`, `infra`, `agents`
- **Shell scripts:** `#!/usr/bin/env bash`, `set -euo pipefail`, source `lib/common.sh`
- **Templates:** `{{ env "VAR" }}` syntax, `private_` prefix for 600 perms
- **Run scripts:** `run_once_*` for one-time ops only, `run_onchange_*` for everything else with hash triggers
- **Env vars:** `DOTFILES_` prefix for dotfiles-system toggles, `DOTFILES_RUN_*_SETUP` for script gates
- **Secrets:** Never committed. `~/.env` is single source of truth. No age/GPG encryption (local repo).
- **Shell indent:** 2-space (not tabs), enforced by `.editorconfig` + `shfmt`
- **Lint/format:** `shellcheck`, `shfmt`, `pre-commit` (runs local/offline `make lint`), `black`, JSON/YAML/Large files validation via `Makefile`
- **Cross-platform:** `brew --prefix` pattern for all Homebrew paths (Intel/ARM agnostic). Network failures in `chezmoi apply` scripts warn, never abort.

## Toggles

Set in `~/.env` (0 = skip, 1 = run):

| Toggle | Purpose | Default |
|--------|---------|---------|
| `DOTFILES_RUN_PACKAGES_SETUP` | Homebrew + winget package installs | 0 |
| `DOTFILES_RUN_MCP_SETUP` | MCP config deployment | 0 |
| `DOTFILES_RUN_OPENCODE_SETUP` | OpenCode secrets + tier config | 0 |
| `DOTFILES_RUN_MACOS_DEFAULTS_SETUP` | macOS user preferences | 0 |
| `DOTFILES_RUN_MACOS_SECURITY_SETUP` | macOS security defaults (firewall, FileVault, etc.) | 0 |
| `DOTFILES_RUN_MERIDIAN_SETUP` | Meridian launchd plist | 0 |
| `DOTFILES_RUN_CADDY_SETUP` | Caddy LAN exposure + Plannotator | 0 |
| `DOTFILES_RUN_OPENCODE_WEB_SETUP` | Optional OpenCode web LaunchAgent | 0 |
| `DOTFILES_RUN_OPENCODE_TOOLS_SETUP` | OpenCode plugins + CLI tools | 0 |
| `DOTFILES_RUN_VOICE_SETUP` | Voice STT/TTS dependencies (whisper-cpp, sox, piper, models) | 0 |
| `DOTFILES_RUN_PLANNOTATOR_SETUP` | Plannotator install/update | 0 |
| `DOTFILES_RUN_JUNIE_CLI_SETUP` | Junie CLI EAP install | 0 |
| `DOTFILES_RUN_SMALLCODE_SETUP` | SmallCode CLI install + config | 0 |
| `DOTFILES_RUN_MOZART_SETUP` | Mozart router config | 0 |
| `DOTFILES_RUN_CODEGRAPH_SETUP` | CodeGraph MCP registration | 0 |
| `DOTFILES_RUN_AGENT_GUIDANCE_SETUP` | Agent guidance distribution | 0 |
| `DOTFILES_RUN_SECRETS_SETUP` | Secrets distribution helper | 0 |
| `DOTFILES_RUN_SKILLS_SETUP` | Skills distribution to all agent directories | 0 |
| `DOTFILES_RUN_OLLAMA_DAEMON_SETUP` | Ollama daemon env config | 0 |
| `DOTFILES_USE_LOCAL_OLLAMA` | Include local Ollama in OpenCode | 1 |
| `DOTFILES_MIN_REASONING_EMBEDDING` | Min embedding_length for reasoning/solo (0 = disabled) | 0 |
| `OPENSPEC_TELEMETRY` | OpenSpec telemetry opt-out | 0 |
| `DO_NOT_TRACK` | Global telemetry opt-out | 1 |

## Structure

```
~/Development/dotfiles/           # chezmoi source directory
├── .chezmoi.toml.tmpl            # chezmoi config (sourceDir, minimumVersion)
├── .chezmoiignore                # ignore patterns (scripts/, configs/, macOS-only)
├── .chezmoidata/
│   └── categories.yaml           # Brewfile + wingetfile category toggles
├── .github/                        # GitHub templates, workflows, dependabot
│   ├── PULL_REQUEST_TEMPLATE.md
│   ├── ISSUE_TEMPLATE/
│   ├── workflows/ci.yml
│   └── dependabot.yml
├── .chezmoiscripts/              # 28 scripts: run_once_01-03 (one-time) + run_onchange_04-28 (hash-triggered)
│   ├── # Phase 1: One-time setup (01-03)
│   ├── # Phase 2: Package/CLI installs (04-11)
│   └── # Phase 3: Tool configuration (12-28)
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
├── private_dot_aws/credentials.tmpl        # AWS credentials (2 profiles)
├── private_dot_gnupg/
│   ├── gpg.conf.tmpl              # GPG config (default-key from env)
│   └── gpg-agent.conf             # GPG agent config
├── private_dot_npmrc.tmpl        # npm auth tokens
├── private_dot_ssh/config        # SSH config
├── private_dot_vuescanrc.tmpl    # VueScan license
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
│   │   ├── smallcode.json          # SmallCode — MCP server (stdio)
│   │   ├── codegraph.json          # CodeGraph — local-first semantic code index (stdio MCP)
│   │   └── templates/            # Symlinks → ../ for configure-mcp-tool.sh
│   ├── iterm2/Default.json.tmpl   # iTerm2 Dynamic Profile template (tmux command, non-rewritable)
│   ├── mozart-router/mozart.json # Mozart AI router gateway config
│   └── opencode/
│       ├── oh-my-opencode-slim.json  # Presets, council, fallbacks, tier overrides
│       ├── anthropic-models.json     # Relocated
│       ├── role-to-local-category.json # New
│       ├── openai-models.json        # New
│       └── ollama-cloud-models.json  # New
│   ├── skills/                       # Skill source files (distributed to all agent dirs)
│   │   └── iamhumans/SKILL.md        # Humanization skill for LLM conversations
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
    │   ├── discover_models.py     # Local Ollama discovery to JSON in Python
    │   ├── constants.py           # BASE_URLS, provider URLs, Meridian/Ollama helpers
    │   ├── file_utils.py          # backup_file(), write_text_file()
    │   ├── opencode_config.py     # get_available_tiers(), build_tier_args()
    │   ├── tier_detect.sh         # Shared tier auto-detection (detect_tier)
    │   └── tier_args.sh           # Shared local fallback arg forwarding
    ├── configure-mcps.py       # Generate MCP configs for all AI tools
    ├── configure-jetbrains-ai.py  # JetBrains AI: models, dirs, symlinks, MCP
    ├── configure-opencode-project.py # Write project-specific OpenCode config overrides
    ├── configure-mozart-router.py # Configure Mozart AI router
    ├── configure-secrets.py            # Resolve paths/secrets for AI tool .env files
    ├── configure-all.sh           # Full orchestration wrapper (rebuild configs)
    ├── configure-skills.py          # Distribute skills to all agent skill directories
    ├── verify-config.py           # Verify generated config presence and freshness
    ├── check-hashes.py            # Audit hash trigger coverage
    ├── configure-jetbrains-workspace-project.py # Configure AI dirs in JB workspace modules
    ├── verify-brewfile-completeness.py # Verify Brewfile completeness
    ├── detect-ij-mcp.py           # Detect JetBrains MCP server paths (SSE default)
    ├── configure-mcp-tool.py      # Generate MCP config for a single tool
    ├── configure-meridian.py      # Add Meridian proxy to OpenCode config
    ├── configure-opencode.py      # Write OpenCode config (local ollama default)
    ├── configure-opencode-tier.py # Switch active preset tier (source of truth)
    ├── configure-opencode-voice.py # Write voice plugin config (tui.json, tier-aware)
    ├── get-tools.py               # Get MCP tool registry keys
    ├── install-opencode.sh        # Install OpenCode plugins and tools (incl. voice)
    ├── install-nvm-lts.sh         # Reinstall all LTS node versions
    ├── meridian-launch.sh         # Launch wrapper for meridian (Keychain-aware)
    ├── configure-smallcode.py      # Configure SmallCode (env + TOML + MCP, tier-aware)
    ├── install-smallcode.sh        # Install SmallCode CLI + plugins
    └── generate-jetbrains-profiles.py # Generate model profiles JSON files
```

## Secrets Management

All secrets live in `~/.env` using `KEY='VALUE'` format. Templates use `{{ env "VAR" }}` syntax.

**`.env.example`** in the repo documents all available keys. Run `make drift` to report drift and `make migrate` to append newly documented keys to `~/.env` as commented examples without overwriting secrets.

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

### Provider Base-URL Overrides

Official SDK environment variables override hardcoded provider URLs across all scripts. When set, these take priority over `BASE_URLS` defaults in `constants.py`:

| Env Var | Provider | Default | Notes |
|---------|----------|---------|-------|
| `ANTHROPIC_BASE_URL` | Anthropic | `https://api.anthropic.com/v1` | Also signals Meridian usage when set |
| `OPENAI_BASE_URL` | OpenAI | `https://api.openai.com/v1` | OpenAI SDK standard |
| `OLLAMA_HOST` | Ollama (local) | `http://localhost:11434` | Scheme+host[:port]; overrides `OLLAMA_LOCAL_HOST`/`PORT` |
| `OLLAMA_CLOUD_BASE_URL` | Ollama Cloud | `https://ollama.com/v1` | Non-standard (our env var, not official SDK) |

### Local Service Host/Port Overrides

Local services (Ollama, Meridian) support host/port env var overrides:

| Service | Host Env Var | Port Env Var | Default |
|---------|-------------|-------------|---------|
| Local Ollama | `OLLAMA_LOCAL_HOST` | `OLLAMA_LOCAL_PORT` | `localhost:11434` |
| Official Ollama | `OLLAMA_HOST` | — | `http://localhost:11434` | Scheme+host[:port]; overrides `OLLAMA_LOCAL_HOST`/`PORT` |
| Meridian proxy | `MERIDIAN_HOST` | `MERIDIAN_PORT` | `127.0.0.1:3456` |

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

Homebrew is installed automatically if missing (via the package-install run_onchange script). On Linux, Homebrew installs to `/home/linuxbrew/.linuxbrew` and `brew shellenv` is sourced automatically. Only `brew` entries run on Linux; `cask` entries are macOS-only and skipped.

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
| Adobe Creative Cloud | `adobe-creative-cloud` (cask) | `Adobe.AdobeCreativeCloud` |
| chezmoi | `chezmoi` | `twpayne.chezmoi` |
| Cyberduck | `cyberduck` (cask) | `Cyberduck.Cyberduck` |
| Go | `go` | `GoLang.Go` |
| Google Drive | `google-drive` (cask) | `Google.GoogleDrive` |
| GitHub CLI | `gh` | `GitHub.cli` |
| jq | `jq` | `jqlang.jq` |
| ngrok | `ngrok` (cask) | `ngrok.ngrok` |
| nvm | `nvm` | `CoreyButler.NVMforWindows` |
| OBS Studio | `obs` (cask) | `OBSProject.OBSStudio` |
| PowerShell | `powershell` | `Microsoft.PowerShell` |
| Sublime Text | `sublime-text` (cask) | `SublimeHQ.SublimeText.4` |
| uv | `uv` | `astral-sh.uv` |
| Webex | `webex` (cask) | `Cisco.Webex` |
| wget | `wget` | `JernejSimoncic.Wget` |
| yq | `yq` | `MikeFarah.yq` |
| Zoom | `zoom` (cask) | `Zoom.Zoom` |
| Geekbench | `geekbench` (cask) | `PrimateLabs.Geekbench.5` / `PrimateLabs.Geekbench.6` |
| Steam | `steam` (cask) | `Valve.Steam` |

---

## AI

### Model Tiers

Eleven presets for AI agents, defined in `scripts/configure-opencode-tier.py` (source of truth) and documented in `AGENTS.md`:

| Tier | Providers | Best For |
|------|-----------|----------|
| **pro** | Ollama Cloud | Daily coding, budget mode |
| **pro-plus** | Ollama Cloud + OpenAI (`gpt-5.6-sol`, `gpt-5.6-luna`) | General development |
| **pro-plus-anthropic** | Anthropic + Ollama Cloud + OpenAI | Heavy orchestration |
| **plus** | OpenAI only (`gpt-5.6-terra`, `gpt-5.6-sol`, `gpt-5.6-luna`) | OpenAI-first workflow |
| **plus-anthropic** | OpenAI + Anthropic (no Ollama Cloud) | OpenAI + Anthropic hybrid |
| **anthropic** | Anthropic only (`sonnet-5`, `fable-5`, `haiku-4-5`, `opus-4-6`) | Anthropic-first workflow |
| **local-pro** | Local Ollama (all 4 categories) | Power users with diverse local models |
| **local** | Local Ollama (reasoning + code-gen + lightweight + vision) | Balanced offline/air-gapped |
| **local-mini** | Local Ollama (code-gen + lightweight + vision) | Minimal model diversity |
| **local-nano** | Local Ollama (single code-gen model + vision) | Single-model systems |
| **local-solo** | Local Ollama (single omnicapable model) | Maximum per-request quality, single-model simplicity |

Cloud presets (pro, pro-plus, pro-plus-anthropic) use Ollama Cloud models (e.g. `glm-5.2`, `glm-5.1`, `kimi-k2.6`, `kimi-k2.7-code`, `deepseek-v4-pro`). The `plus` preset uses OpenAI models exclusively. The `plus-anthropic` preset uses OpenAI + Anthropic models without Ollama Cloud. The `anthropic` preset uses Anthropic models exclusively. The `local-pro` preset uses all four `_local:<category>` placeholders resolved at runtime. The `local` preset uses reasoning + code-gen + lightweight + vision. The `local-mini` preset reduces to code-gen + lightweight + vision. The `local-nano` preset uses a single code-gen model for all roles (except vision). The `local-solo` preset uses a single omnicapable model (completion+thinking+tools+vision) for all roles.

**Variant policy:** oracle/council roles use `max` or `xhigh` (for models whose default is already high, like `fable-5`). Orchestrator gets no variant (default). Lightweight roles (librarian, explorer, observer) use `low`. Designer uses `medium`. Fixer uses `high` (code-specialized). See `AGENTS.md` for the full variant convention table.

Switch tier: `scripts/configure-opencode-tier.py <tier>` (pro, pro-plus, pro-plus-anthropic, plus, plus-anthropic, anthropic, local-pro, local, local-mini, local-nano, local-solo)

Default preset: tier auto-detected from available API keys during OpenCode configuration. Auto-detection order: both keys → pro-plus-anthropic, Anthropic only → anthropic, OpenAI only → plus, no keys but Ollama → local, nothing → pro. Local-pro, local-mini, local-nano, and local-solo are manual-only (set via `DOTFILES_OPENCODE_TIER`).

Local Ollama fallbacks are appended by default (use `--no-local-fallbacks` to omit). Fallbacks append **role-appropriate** local models per agent: reasoning models to oracle, code-gen models to orchestrator/fixer/designer, lightweight models to librarian/explorer, vision-capable models to observer. All indexed models matching a role's category are included (not just the best model). Classification uses name heuristics (r1/think/qwq → reasoning, coder/code/devstral → code-gen, mini/phi/smol → lightweight) with size-aware rules, `ollama show` parameter-based classification, and capability filtering. Override per-role: `--local-fallback-role observer=ollama/qwen3.5:9b-mlx`. Override fallback preset: `--local-fallback-preset local-pro`. Override placeholder categories: `--local-fallback-placeholder reasoning=code-gen`. Environment variables: `DOTFILES_LOCAL_FALLBACK_PRESET`, `DOTFILES_LOCAL_FALLBACK_PLACEHOLDERS` (comma-separated), `DOTFILES_LOCAL_FALLBACK_ROLES` (comma-separated).

### DCP Context Compaction

`~/.config/opencode/dcp.json` uses percentage-based thresholds:
- Compress at **67%** of context window
- Leave at least **20%** filled

No per-model config needed — the plugin reads context windows from provider configs.

**TUI panel (v3.1.13+):** `/dcp` opens a context/stats/manual-mode panel. Requires DCP in `tui.json` (written by `scripts/configure-opencode-dcp.py`). Core compression still loads from `opencode.json`. See [docs/DCP.md](docs/DCP.md).

### Voice Plugin (opencode-voice)

OpenCode voice support via [`@renjfk/opencode-voice`](https://github.com/renjfk/opencode-voice) — a TUI-only plugin for voice input (STT) and output (TTS).

**Configuration:** `~/.config/opencode/tui.json` (written by `configure-opencode-voice.py`, tier-aware)

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
| **plus** | `gpt-5.6-luna` via OpenAI | OpenAI STT |
| **plus-anthropic** | `gpt-5.6-luna` via OpenAI | OpenAI STT |
| **anthropic** | Meridian proxy or `claude-haiku-4-5` | whisper-cli (local), OpenAI STT if key available |

**Meridian detection:** If `is_meridian_configured()` returns true (i.e., `MERIDIAN_API_KEY` or `ANTHROPIC_BASE_URL` is set), the Anthropic tier routes through Meridian. When `ANTHROPIC_BASE_URL` is set, its value is used directly as the endpoint.

**Local STT/TTS dependencies** (installed by `scripts/install-opencode.sh` step 8, gated on `DOTFILES_RUN_VOICE_SETUP=1`):

| Component | Install | Purpose |
|-----------|---------|---------|
| `whisper-cpp` | `brew install whisper-cpp` | Local speech-to-text |
| `sox` | `brew install sox` | Audio format conversion |
| `piper-tts` | `uv tool install piper-tts` | Local text-to-speech |
| Whisper model | Download to `~/.local/share/whisper-cpp/` | STT model (default: `ggml-large-v3-turbo.bin`) |
| Piper voice | Download to `~/.local/share/piper-voices/` | TTS voice (default: `en_US-lessac-high`) |

Model defaults are configurable: `DOTFILES_WHISPER_MODEL` and `DOTFILES_PIPER_VOICE`.

Configure voice: `scripts/configure-opencode-voice.py --preset <tier>`

### SmallCode

[SmallCode](https://github.com/Doorman11991/smallcode) is a terminal-native coding agent for small local models (8B–35B). It provides budgeted context, forgiving tool-call parsing, search/replace patching, persistent memory, and adaptive cloud escalation.

**Configuration:** `~/.config/smallcode/` (env + TOML + MCP, written by `configure-smallcode.py`, tier-aware)

| SmallCode Slot | OpenCode Role |
|---------------|-------------|
| DEFAULT | orchestrator |
| FAST | librarian |
| MEDIUM | fixer |
| STRONG | oracle |

Escalation uses the STRONG/oracle model and its provider. After 3+ calls, if failure rate >0.3 → MEDIUM, >0.6 → STRONG.

- **Install:** `scripts/install-smallcode.sh` (gated on `DOTFILES_RUN_SMALLCODE_SETUP=1`)
- **Configure:** `scripts/configure-smallcode.py --preset <tier>` writes `.env`, `smallcode.toml`, and `mcp.json`
- **Context budget:** `SMALLCODE_CONTEXT_BUDGET=67` (aligned with OpenCode's DCP threshold)
- **Meridian routing:** Anthropic models route through Meridian when `is_meridian_configured()` returns true
- **MCP:** `smallcode --mcp` (stdio integration)
- **Shell:** `smallcode` passthrough wrapper in `aliases.sh`

### Mozart Router

Local AI gateway router. `scripts/configure-mozart-router.py` is the sole writer of `~/.mozart/mozart.json` (the template was removed to avoid dual-source conflicts).

- **Install:** `npm install -g mozart-router`
- **Configure:** `scripts/configure-mozart-router.py` writes `~/.mozart/mozart.json`
- **Gateways:** Ollama Cloud, OpenAI, and Anthropic Meridian — all via GenericOpenAI adapter
- **Provider URL overrides:** Gateways support `baseUrlEnv` keys — if the named env var is set, it overrides the hardcoded `baseUrl` (resolved and stripped before writing). Supported overrides: `OLLAMA_CLOUD_BASE_URL`, `OPENAI_BASE_URL`, `ANTHROPIC_BASE_URL`.
- **Meridian proxy:** Uses `MERIDIAN_HOST` and `MERIDIAN_PORT` env vars (defaults: `127.0.0.1` and `3456`) for the local Meridian endpoint
- **Usage:** `mozart-router doctor`, `mozart-router route "task description"`, `mozart-router proxy --port=4445`
- **MCP:** `mozart-router mcp` (stdio integration)
- Gated by `DOTFILES_RUN_MCP_SETUP`

### Plannotator

Plannotator CLI is installed via the existing install script. The paste backend + static portal are installed by `scripts/install-plannotator.sh` and the `run_onchange_25` LaunchAgent when `DOTFILES_RUN_CADDY_SETUP=1`. OpenCode plugin (`@plannotator/opencode@latest`) is already configured in global `opencode.json`. Use `/plannotator-review`, `/plannotator-annotate`, `/plannotator-last` in OpenCode.

### OpenSpec

Spec-driven development (SDD) for AI coding assistants.

- Install: `npm install -g @fission-ai/openspec@latest`
- Init: `cd your-project && openspec init`
- Commands: `/opsx:propose`, `/opsx:apply`, `/opsx:archive`
- Plugin: `opencode-plugin-openspec` adds the `openspec-plan` agent for read-only planning
- Telemetry: `OPENSPEC_TELEMETRY=0` and `DO_NOT_TRACK=1`

### Junie CLI

JetBrains EAP CLI installed via the Junie CLI install script. Cross-platform: `curl|bash` on Mac/Linux, PowerShell on Windows.

#### Junie Model Profiles

Generated dynamically by `scripts/configure-jetbrains-ai.py --models` from `configs/junie/model-groups.json`:

| Profile | Provider | Primary | Faster | Temp |
|---------|----------|---------|--------|------|
| `pro` | cloud | `glm-5.2` | `gemma4:31b` | 0.7 |
| `pro-plus` | cloud | `glm-5.2` | `gpt-5.6-luna` (openai) | 0.7 |
| `pro-plus-anthropic` | meridian | `claude-sonnet-5` | `claude-haiku-4-5` | 1 |
| `anthropic` | meridian | `claude-sonnet-5` | `claude-haiku-4-5` | 1 |
| `plus` | openai | `gpt-5.6-terra` | `gpt-5.6-luna` | 1 |
| `plus-anthropic` | openai | `gpt-5.6-terra` | `claude-haiku-4-5` (meridian) | 1 |
| `local-pro` | local | `_local:reasoning` | `_local:lightweight` | 0.6 |
| `local` | local | `_local:code-gen` | `_local:lightweight` | 0.6 |
| `local-mini` | local | `_local:code-gen` | `_local:vision` | 0.6 |
| `local-nano` | local | `_local:code-gen` | — | 0.6 |
| `local-solo` | local | `_local:solo` | `_local:solo` | 0.6 |
| `meridian-opus` | meridian | `claude-opus-4-6` | — | 1 |
| `meridian-sonnet` | meridian | `claude-sonnet-5` | — | 1 |
| `meridian-haiku` | meridian | `claude-haiku-4-5-20251001` | — | 1 |
| `meridian-fable` | meridian | `claude-fable-5` | — | 1 |

Local Ollama profiles resolve model IDs dynamically via `ollama ls` prefix matching. Cloud profiles use hardcoded IDs from `model-groups.json`. Temperatures follow Junie's recommendations.

Homebrew-aware scripts now prefer `brew --prefix` so they stay Intel/ARM agnostic.

Select via: `junie --model custom:<profile>`

### MCP Configuration

Centralized in `configs/mcp/`. `global-mcps.json` maps 7 AI tools to MCP templates, plus shared `smallcode` and `codegraph` server templates. `configure-mcps.py` generates per-tool config files.

| Tool | Config Path | Format |
|------|------------|--------|
| OpenCode | `~/.config/opencode/opencode.json` | JSON internal (global: github, idea, sentry) |
| JetBrains AI | `~/.ai/mcp/mcp.json` | JSON mcpServers (global: github, idea, sentry) |
| Junie | `~/.ai/mcp/mcp.json` | JSON mcpServers (global: github, idea, sentry, via `.junie → .ai` symlink) |
| Air | `~/.ai/mcp/mcp.json` | JSON mcpServers (global: github, idea, sentry, shares `.ai` path) |
| Cursor | `~/.cursor/mcp.json` | JSON mcpServers (global: github, idea, sentry) |
| Codex | `~/.codex/config.toml` | TOML (global: github, idea, sentry) |
| Gemini | `~/.gemini/settings.json` | JSON merge (global: github, sentry) |

Global MCP servers: github, idea, sentry, smallcode, codegraph. Project-level MCP servers (betterstack, mongodb, shortcut, notion) are configured per-project via `configure-mcp-tool.py --mode project`.

`idea.json` uses SSE transport by default. Set `IJ_MCP_TRANSPORT=stdio` and run `detect-ij-mcp.py` for stdio mode. The MCP configure script sources its output before the gate check.

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
- macOS security defaults gated by `DOTFILES_RUN_MACOS_SECURITY_SETUP=1`
- Meridian launchd gated by `DOTFILES_RUN_MERIDIAN_SETUP=1`

### Linux

- Homebrew installs to `/home/linuxbrew/.linuxbrew`, sourced via `brew shellenv`
- Only `brew` formulae install; `cask` entries are skipped
- Use `$(brew --prefix)` for Homebrew path resolution — no hardcoded platform paths

### Windows

- Package management via `winget` and `wingetfile*` category bundles
- `the package-install run_onchange script` handles both `brew bundle` and `winget install` (Windows-only section)
- Windows winget install is gated by the same `DOTFILES_RUN_PACKAGES_SETUP` toggle as Homebrew
