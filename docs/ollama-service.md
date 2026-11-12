# Ollama package ownership and headless service cutover

**Status (2026-11-12):** Source-level, default-off **package selection only**. The new `local_ai_headless` category installs the Homebrew `ollama` formula on macOS or Linux when `DOTFILES_RUN_PACKAGES_SETUP=1`; it does **not** start, stop, configure or migrate a daemon. A real host cutover has **not** been executed.

## Ownership contract

| Installation | Package owner | Supervisor | Existing dotfiles configuration |
| --- | --- | --- | --- |
| macOS desktop Ollama.app | `Brewfile.desktop.dev`: `ollama-app` cask | Ollama.app itself | Script 27's `com.dotfiles.ollama-env` login agent |
| macOS headless Ollama (opt-in) | `Brewfile.local.ai.headless`: `ollama` formula | `brew services` / launchd user agent | No managed service-environment cutover yet |
| Linux headless Ollama (opt-in) | Same `ollama` Homebrew formula | `brew services` / systemd user unit (or an existing native systemd unit) | Script 27's existing systemd override lane remains unchanged |
| Windows Ollama | `Ollama.Ollama` in existing WinGet categories | Ollama's native Windows app/service behaviour | Script 27's `setx` path; WinGet does not supervise services |

The Homebrew formula and desktop cask may both expose an `ollama` executable. Having both installed is **not** a working migration. The macOS package preflight refuses the `desktop_dev` category or an existing `Ollama.app` or known Ollama cask before any Brewfile is installed. It does not remove the app or its user data.

## Owner-run evaluation (no automatic cutover)

1. On the target host, capture `command -v ollama`, `ollama --version`, `brew list --versions ollama`, `brew list --cask --versions ollama-app`, `brew services info ollama`, `ollama list`, `lsof -nP -iTCP:11434 -sTCP:LISTEN`, and the active daemon owner. Record `~/.ollama` model-store location and permissions without copying model data or credentials to a PR.
2. Back up private settings and `~/Library/LaunchAgents/com.dotfiles.ollama-env.plist` if they exist. Stop active inference work, and decide which owner should control the single listener. **Do not enable** both `desktop_dev` and `local_ai_headless` on macOS. If Ollama.app is installed, explicitly uninstall/retire it only after recording rollback and ensuring the service is idle; the repository will refuse to choose for you.
3. To enable the formula category, use `make categories` or `python3 scripts/show-categories.py --enable local_ai_headless` on a prepared host, set `DOTFILES_RUN_PACKAGES_SETUP=1` in the private `~/.env`, and preview with `make dry-run`. Running `make deploy` installs the formula but deliberately does **not** activate `brew services`. Alternatively, with the category still off, test `brew install ollama` manually on a disposable host without Ollama.app.
4. **Manual service pilot only:** after verifying the formula binary and that port 11434 is unoccupied, use `brew services start ollama`. This registers a *user-login* service; a true boot-time system service would require elevated registration and separate authorization. Confirm the process executable, listener, `ollama list` and a real inference request. Do not use `sudo brew services` for this pilot.
5. Homebrew supports `$HOMEBREW_USER_CONFIG_HOME/services/ollama.env` (normally `~/.homebrew/services/ollama.env`) with `KEY=value` lines and `brew services restart ollama` to apply them. **This PR does not write that file**. The existing Script 27 configures Ollama.app through `launchctl setenv` and its own LaunchAgent; do not run Script 27 as a service-environment migration. A later PR must add guarded, preservation-safe environment reconciliation and remove legacy launchd ownership only after an owner-approved cutover.
6. Test login persistence, port-binding errors, expected `OLLAMA_* ` tuning, local-model inventory, local model inference and downstream oMLX/LiteLLM/OpenCode/Pi consumers. These tests are **NOT RUN** here. Never claim the process is correctly configured based only on a successful formula installation.

## Rollback and deferred work

If the manual headless pilot fails, stop only the Homebrew-owned user service with `brew services stop ollama`, restore the previously recorded service owner and settings using the owner's backup, and verify inference/health. Do **not** delete `~/.ollama`, pull models, alter `OLLAMA_HOST` exposure, remove credentials, or restart the desktop app without operator intent.

The next independent PR should implement and test the actual macOS Homebrew `ollama.env` lane, explicit service-owner detection, conflict/rollback checks, and a guarded `make ollama-restart` contract. A subsequent LiteLLM packaging comparison must preserve its pinned Python/Prisma runtime, provider mapping and virtual keys; none of those are changed here.

Upstream: [Homebrew Ollama formula](https://formulae.brew.sh/formula/ollama), [Ollama app cask](https://formulae.brew.sh/cask/ollama-app), [Homebrew services](https://docs.brew.sh/Manpage#services-subcommand).
