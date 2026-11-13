# Installation and upgrading

> Step-by-step deployment and verification for new environments. See [AGENTS.md](../AGENTS.md) for repository policy.

AI agents should adhere to the following sequence for deploying and verifying the repository across different environments:

### Prerequisites
- **macOS / Linux:** Homebrew (`brew`)
- **Windows:** PowerShell 7 (`pwsh`), Windows Package Manager (`winget`)

### Step 1: Repository Cloning
Clone the repository to your local development workspace (standard target is `~/Development/dotfiles` or `$HOME\Development\dotfiles`):
```bash
mkdir -p ~/Development
git clone https://github.com/randytarampi/dotfiles.git ~/Development/dotfiles
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
   DOTFILES_RUN_PACKAGES_SETUP=1
   ```

### Step 3: Makefile + chezmoi Orchestration
Initialize chezmoi, then use `make deploy` so `~/.env` is loaded in the same shell process as each chezmoi command:

**Homebrew first install (macOS/Linux):** `DOTFILES_RUN_PACKAGES_SETUP=1` is an **explicit, mutating opt-in**. The package script resolves brew after installation (known Intel/Apple Silicon/Linux locations or `HOMEBREW_PREFIX`), fails on installer/shellenv errors and refuses to run any bundle without a real brew executable. Default `dev` includes platform-specific formulas; **Linux and ARM package-manifest completeness remains unqualified** ([#272](https://github.com/randytarampi/dotfiles/issues/272)). On minimal/headless machines keep the package gate disabled, install prerequisites with the OS package manager, and inspect selected manifests before opting in.

**Headless Ollama (macOS/Linux):** `local_ai_headless` is a separate default-off Homebrew formula category and does not auto-start a daemon. Before opting in, see [Ollama package ownership and cutover](ollama-service.md); the macOS package lane refuses an installed Ollama.app or a simultaneously selected `desktop_dev` category.

**Windows package manifest contract:** `wingetfile*` files contain one exact WinGet package ID per non-comment line, not WinGet JSON exports. The package setup gate installs them with `winget install --id <ID> --exact` rather than `winget import`. `DOTFILES_WINGETFILE_IGNORE` excludes exact comma-separated IDs. This installs/upgrades packages, not Windows Services or scheduled tasks. Windows-host WinGet behaviour remains a separate acceptance test.

**Windows qualification:** The Makefile invokes Bash and Unix utilities. Winget/PowerShell can bootstrap packages, but Makefile targets require a prepared POSIX-compatible environment (such as Git Bash or WSL with appropriate paths, Make and the required CLI tools). Native PowerShell alone is not a proven deployment or verification path. Validate the installed host rather than assuming cross-shell parity.

The Codex configuration step in `make deploy` requires Poetry and this repository's locked production Python dependencies **when Codex is installed, an existing `~/.codex/config.toml` (or `CODEX_HOME/config.toml`) is present, or `DOTFILES_RUN_CODEX_SETUP=1`**. If all three are absent, the Codex step is skipped and a core deployment does not require Poetry for Codex. If Codex configuration is selected, install Poetry with your platform package manager or its official installer, then run `poetry install --only main --no-root` from the checkout. Missing Poetry/TOML dependencies stop the selected Codex step rather than silently downgrading. `make configure` has the same rule; use `bash scripts/configure-all.sh --skip codex` only when deliberately excluding Codex. An installed Codex CLI is not itself proof that provider inference is configured or authenticated.

The staged cross-platform clean-host qualification plan is in [Clean bootstrap acceptance](clean-bootstrap.md). Current CI tests disposable home deployment on Ubuntu, macOS and Windows/Git Bash, **not** full OS/package provisioning. Windows PowerShell-only setup and legacy Catalina support are not yet qualified.

- **macOS / Linux:**
  ```bash
  command -v chezmoi >/dev/null 2>&1 || brew install chezmoi
  chezmoi init --source ~/Development/dotfiles
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

On first setup, run `make deploy` twice. The second pass should be a no-op, but it helps surface any idempotency gaps in the templates, scripts, or configure generators.

### Project Setup (Optional)

For project-specific AI configuration, create `.opencode/.env` in the project (see [project-env.example](project-env.example)), then run:

```bash
scripts/configure-project.py
# Default steps: opencode,codegraph,skills,jetbrains,junie.
# Or only reconcile project skills:
scripts/configure-project.py --steps skills
```

Project configuration values select the tier, skills, MCPs, ACP agents, CodeGraph, JetBrains, Junie, and project secrets. `.opencode/.env` is the user-authored declaration; generated secrets go to the gitignored `.opencode/.env.local`. CLI arguments take precedence over project and global environment values.

### Core clean-host smoke (Linux, synthetic HOME)

`make bootstrap-smoke` runs the actual deployment twice in a disposable HOME, checks chezmoi drift (including a failed diff command), and stubs known package-manager, download and service-control commands. It does **not** block all outbound network traffic (such as direct Python sockets); see [#281](https://github.com/randytarampi/dotfiles/issues/281) for network-contained acceptance. It requires bash, make, Python 3 and chezmoi **but not Poetry, Codex or credentials**. The required CI matrix uses explicit Ubuntu 24.04 and 26.04 runners on x64 and ARM64.

A separate **required**, representative Ubuntu 24.04 x64 container check builds an unprivileged image from the committed checkout, then executes the same two-pass bootstrap with `docker run --network none`. Before deployment it verifies the absence of a default IPv4 route and attempts a direct, non-stubbed TCP socket connection to `1.1.1.1:443` that must fail with a network-unreachable error. Native version/architecture smoke remains independently required but is not itself a network sandbox.

For optional, committed-tree-only Docker reproduction (avoid adding untracked credentials to the image context):

```bash
git archive HEAD | docker build --platform linux/amd64 --build-arg UBUNTU_VERSION=24.04 -f tests/bootstrap/Dockerfile -t dotfiles-core:24-amd64 -
docker run --rm --network none --platform linux/amd64 dotfiles-core:24-amd64

git archive HEAD | docker build --platform linux/arm64 --build-arg UBUNTU_VERSION=26.04 -f tests/bootstrap/Dockerfile -t dotfiles-core:26-arm64 -
docker run --rm --network none --platform linux/arm64 dotfiles-core:26-arm64
```

This is **not** proof that the full Homebrew/WinGet category sets install or that Docker emulation reproduces a physical Raspberry Pi/Pine64. The CLI toolchain is installed into the disposable image explicitly; native VM and owner-hardware acceptance remain separate.

### Step 4: Verification & Local Linting (Optional)
To verify repository and script health offline:
- **macOS / Linux:**
  ```bash
  # Install standard dev dependencies (black, shellcheck, shfmt, pre-commit)
  brew bundle --file Brewfile.dev

  # Set up local hooks
  pre-commit install

  # Run verification
  make verify
  # or
  pre-commit run --all-files
  ```
- **Windows (PowerShell 7 / Git Bash):**
  ```powershell
  # Install dev tools via winget (automatic via DOTFILES_RUN_PACKAGES_SETUP=1 on chezmoi apply)
  # Or install manually:
  winget install GnuWin32.Make OpenJS.NodeJS Python.Python.3.12 psf.black koalaman.shellcheck mvdan.shfmt

  # Set up local hooks
  pip install pre-commit
  pre-commit install

  # Run verification (native PowerShell or Git Bash / WSL for Unix utility compatibility)
  make verify
  # or
  pre-commit run --all-files
  ```

---

## Upgrading

After pulling changes that rename gates or restructure scripts:

1. `make migrate-preview` — preview deprecated gate renames without writing. After reviewing, run `make migrate` to apply them and insert any new keys from `.env.example` into their correct sections. This migration requires POSIX ownership and permissions (macOS/Linux); native Windows is refused before file access. An existing `.env.bak` is never overwritten: preserve it in a private location before retrying, or deliberately invoke the migration script with `--apply --no-backup` if an independent recovery copy already exists.
2. Edit `~/.env` to enable any new features (set gates to `1`)
3. `make reset` — clear orphaned chezmoi script state (only needed after script renames)
4. `make deploy` — full rebuild
5. `make verify` — confirm everything is in order
