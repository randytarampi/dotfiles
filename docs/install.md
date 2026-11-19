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

### Previously deployed repository-only files

The source-level `.chezmoiignore` excludes build inputs such as `tests/`, `artifacts/`, the Poetry lockfiles, and `coverage.lcov` from future home-directory deployment. The rule is not a licence to delete existing `~/tests`, `~/artifacts` or other same-named files: a previous deploy may have copied them, but later user changes may make them independent. Inspect the actual files and compare them with the source checkout before any owner-approved clean-up; never recursively remove them as part of `make deploy`. Check `chezmoi managed` and the two-pass diff to confirm the current managed-file contract.

### Step 3: Makefile + chezmoi Orchestration
Initialize chezmoi, then use `make deploy` so `~/.env` is loaded in the same shell process as each chezmoi command:

**Homebrew first install (macOS/Linux):** `DOTFILES_RUN_PACKAGES_SETUP=1` is an **explicit, mutating opt-in**. The package script resolves brew after installation (known Intel/Apple Silicon/Linux locations or `HOMEBREW_PREFIX`), fails on installer/shellenv errors and refuses to run any bundle without a real brew executable. Before any Homebrew operation on Linux, a read-only [selected-manifest audit](linux-brewfile-eligibility.md) rejects known Linux-incompatible casks and malformed entries: the default `dev` category currently contains casks, so Linux users must first deliberately select and review compatible categories rather than allowing a partial default install. Formula/tap and CPU availability remain **unqualified** ([#272](https://github.com/randytarampi/dotfiles/issues/272)); a passing syntax/compatibility preflight does not authorize or certify package installation. On minimal/headless machines keep the package gate disabled, install prerequisites with the OS package manager, and inspect selected manifests before opting in.

**Headless Ollama (macOS/Linux):** `local_ai_headless` is a separate default-off Homebrew formula category and does not auto-start a daemon. Before opting in, see [Ollama package ownership and cutover](ollama-service.md); the macOS package lane refuses an installed Ollama.app or a simultaneously selected `desktop_dev` category.

**Windows package manifest contract:** `wingetfile*` files contain one exact WinGet package ID per non-comment line, not WinGet JSON exports. The package setup gate installs them with `winget install --id <ID> --exact` rather than `winget import`. `DOTFILES_WINGETFILE_IGNORE` excludes exact comma-separated IDs. This installs/upgrades packages, not Windows Services or scheduled tasks. Windows-host WinGet behaviour remains a separate acceptance test.

**Windows qualification:** The Makefile invokes Bash and Unix utilities. Winget/PowerShell can bootstrap packages, but Makefile targets require a prepared POSIX-compatible environment. **Git Bash on Windows is the CI deployment path; WSL2 and PowerShell-only deployment are separate and not qualified.** The required deployment matrix now attempts Windows Server x64 and native Windows 11 ARM64, checking the Windows kernel/CPU identity before using the same chezmoi **v2.72.2** Git Bash interpreter and two-pass deployment. These additional rows count as evidence **only if their exact-head jobs pass**. GitHub's Windows 11 ARM64 VM is not proof of an unmodified retail Windows 11 x64 installation, all WinGet packages, or a physical owner machine. See [#274](https://github.com/randytarampi/dotfiles/issues/274).

**Preview-first Windows/Git Bash interpreter helper (no package or service changes):**

```bash
# Run from the repository checkout in Git Bash, after installing make and chezmoi.
bash scripts/bootstrap/windows-chezmoi-interpreter.sh --print
mkdir -p "$HOME/.config/chezmoi"
bash scripts/bootstrap/windows-chezmoi-interpreter.sh --write "$HOME/.config/chezmoi/interpreters.toml"
bash scripts/bootstrap/windows-chezmoi-interpreter.sh --check "$HOME/.config/chezmoi/interpreters.toml"

# Use the same explicit config for initialization and subsequent deploys.
# CHEZMOI_CONFIG is quoted by Makefile; it also supports paths with spaces.
cz_config="$(cygpath -m "$HOME/.config/chezmoi/interpreters.toml")"
chezmoi --config "$cz_config" init --source "$PWD"
CHEZMOI_CONFIG="$cz_config" make deploy
```

The helper derives the native `bash.exe` location using `cygpath`, validates TOML literal-string safety, previews without writing, refuses to overwrite an existing config (or any symlink) by default, and writes a mode-600 file atomically when explicitly requested. The `--force` override replaces only an existing regular file. **Windows HOME/config-path with spaces:** `CHEZMOI_CONFIG` avoids the unsafe multi-word `CHEZMOI` override. The required Windows Server x64 and Windows 11 ARM64 deployment rows exercise space-bearing synthetic HOME and config paths; count them as hosted-runner evidence only once the exact-head CI passes. Do not overwrite a user's existing chezmoi config; the helper refuses collisions unless explicitly forced. Native PowerShell-only, fresh retail Windows VMs, WinGet provisioning and the actual installed user's profile remain unqualified under #274.

**macOS qualification:** The required deployment matrix retains floating `macos-latest` but adds explicit `macos-15-intel` (x86_64) and `macos-26` (arm64), asserting their OS major version and CPU before the unchanged two-pass deployment. Passing those runner jobs qualifies that GitHub-hosted account/toolchain, **not** all Homebrew formulae, GUI clients or a fresh owner account. macOS 10.15–14 is **legacy core-only, best-effort**; GitHub-hosted macOS 14 images have been retired, so no CI claim is made for those versions. See [#274](https://github.com/randytarampi/dotfiles/issues/274).

The Codex configuration step in `make deploy` requires Poetry and this repository's locked production Python dependencies **when Codex is installed, an existing `~/.codex/config.toml` (or `CODEX_HOME/config.toml`) is present, or `DOTFILES_RUN_CODEX_SETUP=1`**. If all three are absent, the Codex step is skipped and a core deployment does not require Poetry for Codex. If Codex configuration is selected, install Poetry with Homebrew (`brew install poetry`; the brew dev category manages it on macOS and Linux) or from the official installer on hosts without the brew lane (there is no winget package), then run `poetry install --only main --no-root` from the checkout. Missing Poetry/TOML dependencies stop the selected Codex step rather than silently downgrading. `make configure` has the same rule; use `bash scripts/configure-all.sh --skip codex` only when deliberately excluding Codex. An installed Codex CLI is not itself proof that provider inference is configured or authenticated.

The staged cross-platform clean-host qualification plan is in [Clean bootstrap acceptance](clean-bootstrap.md). Current CI tests disposable home deployment on Ubuntu, macOS and Windows/Git Bash, **not** full OS/package provisioning. Windows PowerShell-only setup and legacy Catalina support are not yet qualified.

- **macOS / Linux:**
  ```bash
  command -v chezmoi >/dev/null 2>&1 || brew install chezmoi
  chezmoi init --source ~/Development/dotfiles
  make deploy
  ```
- **Windows (PowerShell 7 for prerequisite inspection, then Git Bash for `make deploy`):**
  ```powershell
  Get-Command git, winget, make, chezmoi -ErrorAction SilentlyContinue
  # Confirm chezmoi --version meets minimumVersion 2.70.2; CI uses v2.72.2.
  # Install missing prerequisites deliberately. Run the Git Bash helper above
  # before deployment; do not run make deploy from plain PowerShell.
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

The separate required `bootstrap-networkless` job exercises a **four-row Ubuntu 24.04/26.04 x64/ARM64 matrix** inside native-platform, unprivileged Docker runtimes with `docker run --network none`. Each row asserts the OS release and CPU both outside and inside the container, uses UID 10001 with dropped capabilities and tests that direct, non-stubbed **IPv4 and IPv6** sockets cannot reach numeric public addresses. It also runs the core deployment twice and requires no chezmoi drift. A failed Docker build, missing ARM runtime or unqualified socket error fails the job instead of quietly shrinking the matrix. Image build-time dependency downloads are outside the no-egress guarantee. These are CI-hosted OS/CPU checks, **not** physical ARM board or native Windows/macOS network isolation; see [#281](https://github.com/randytarampi/dotfiles/issues/281).

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

  # Set up the staged, check-only commit hooks (opt-in)
  make hooks-install

  # Run full verification
  make verify
  ```
- **Windows (PowerShell 7 / Git Bash):**
  ```powershell
  # Install dev tools via winget (automatic via DOTFILES_RUN_PACKAGES_SETUP=1 on chezmoi apply)
  # Or install manually:
  winget install GnuWin32.Make OpenJS.NodeJS Python.Python.3.12 psf.black koalaman.shellcheck mvdan.shfmt

  # Set up the staged, check-only commit hooks (opt-in)
  pip install pre-commit
  make hooks-install

  # Run full verification (native PowerShell or Git Bash / WSL for Unix utility compatibility)
  make verify
  ```

---

## Upgrading

After pulling changes that rename gates or restructure scripts:

1. `make migrate-preview` — preview deprecated gate renames without writing. After reviewing, run `make migrate` to apply them and insert any new keys from `.env.example` into their correct sections. This migration requires POSIX ownership and permissions (macOS/Linux); native Windows is refused before file access. An existing `.env.bak` is never overwritten: preserve it in a private location before retrying, or deliberately invoke the migration script with `--apply --no-backup` if an independent recovery copy already exists.
2. Edit `~/.env` to enable any new features (set gates to `1`)
3. `make reset` — clear orphaned chezmoi script state (only needed after script renames)
4. `make deploy` — full rebuild
5. `make verify` — confirm everything is in order
## Windows deployment executable architectures

The Windows deploy job reports the PE machine type of the executable files used for `bash`, `make`, `chezmoi` and Python. It resolves Git Bash's running shell implementation instead of reporting the launcher, and reports Chocolatey's make shim separately from the target recorded in its adjacent `.shim` metadata. An unavailable or unparseable executable is reported as `unknown`; this diagnostic does not assert architecture compatibility or replace the Windows OS/CPU check.
