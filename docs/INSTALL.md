# Initial Installation & Run Instructions

> Step-by-step deployment and verification for new environments.
> See [AGENTS.md](../AGENTS.md) for the lean agent guidance index.

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
   DOTFILES_RUN_PACKAGES_SETUP=1
   ```

### Step 3: Makefile + chezmoi Orchestration
Initialize chezmoi, then use `make deploy` so `~/.env` is loaded in the same shell process as each chezmoi command:
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

For project-specific AI configuration, create `.opencode/.env` in the project
(see [project-env.example](project-env.example)), then run:

```bash
scripts/configure-project.py
# Or only reconcile project skills:
scripts/configure-project.py --steps skills
```

Project configuration values select the tier, skills, MCPs, ACP agents, CodeGraph,
JetBrains, Junie, and project secrets. `.opencode/.env` is the user-authored
declaration; generated secrets go to the gitignored `.opencode/.env.local`.
CLI arguments take precedence over project and global environment values.

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

1. `make migrate` — rename deprecated gates in `~/.env` to current names and insert any new keys from `.env.example` into their correct sections
2. Edit `~/.env` to enable any new features (set gates to `1`)
3. `make reset` — clear orphaned chezmoi script state (only needed after script renames)
4. `make deploy` — full rebuild
5. `make verify` — confirm everything is in order
