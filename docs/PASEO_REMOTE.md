# Paseo: private remote coding-agent sessions

**Status:** opt-in stable v0.14.0 pilot. Does not enable a public listener, relay, or daemon automatically. Read [REMOTE_ACCESS.md](REMOTE_ACCESS.md) first.

## Owner-run setup

1. Confirm an enrolled Tailscale host and mobile client, with grants limited to the intended daemon port (`6767`). Test OpenCode v2 connectivity independently before installing Paseo.
2. On the development host: `DOTFILES_RUN_PASEO_SETUP=1 bash scripts/install-paseo.sh`. This installs the `@getpaseo/cli@0.14.0` stable CLI for the current user; review npm scope/PATH first. If Desktop Paseo is already installed, avoid creating a competing daemon. The installer verifies that `paseo --version` reports the requested v0.14.0 and otherwise returns failure. Check `paseo --version`, `paseo daemon status` and the process owner.
3. Interactive authentication: `paseo daemon set-password`. Explicitly disable relay with `paseo daemon config set daemon.relay.enabled false` (important for older homes, whose omitted setting may enable relay). Keep the generated bcrypt hash in user-owned `~/.paseo/config.json`; never store the plaintext in the repo, shell history, or a URL query parameter.
4. Run `python3 scripts/configure-paseo-tailnet.py` (read-only). Then `DOTFILES_RUN_PASEO_SETUP=1 python3 scripts/configure-paseo-tailnet.py --apply`. This checks `tailscale ip -4`, requires a structurally valid bcrypt hash and an **explicit** `daemon.relay.enabled: false` setting, refuses a non-default existing listener or an override such as `PASEO_RELAY_ENABLED=true`, and delegates the edit to Paseo's own `daemon config set` command, and verifies no unrelated JSON content changed. Record the original listener and private config backup first.
5. Restart via Paseo's native supervisor (`paseo daemon restart` if already running, otherwise `paseo daemon start`). Test the mobile **direct connection** to the host's Tailscale IP, port 6767, SSL off (Tailscale encrypts transport), with the Paseo password. Do not bind the daemon to `0.0.0.0`.

A stored listen address is not the active socket: foreground `PASEO_LISTEN` overrides and legacy supervisors may retain launch overrides. Check `paseo daemon status` after restart, and require the actual bound IP (not just config JSON) before mobile testing.

The CLI supports the locally installed provider binaries and their existing authentication/skills/MCP configuration. Prove actual OpenCode v2 and Pi tasks in disposable worktrees, then Claude and Codex. Running `com.opencode.web` does not prove Paseo attaches to that session; the v0.15 beta adds reuse of running OpenCode services but is **not** adopted by this pilot.

## Boundaries and negative tests

- The script is intentionally **not** part of `make deploy`; no unattended user-state mutations. No automatic session starts or CLI provider reconfiguration.
- Missing/broken Tailscale, unprotected Paseo daemon, existing custom listener, enabled relay, missing CLI or config: refuse instead of widening access.
- No public Caddy site, Cloudflare Tunnel, Tailscale Funnel or E2EE relay is enabled here. Direct Tailscale TCP access still requires Paseo's own password.
- Test denial for the wrong tailnet identity and wrong app password. Test session continuity after app suspension and after switching between Wi-Fi and cellular; confirm terminal, diff and approval surfaces.

## Rollback

Stop the user-owned Paseo daemon with `paseo daemon stop`. Restore the prior user-controlled `daemon.listen` through `paseo daemon config set daemon.listen 127.0.0.1:6767`, only if that was its previous state; otherwise restore the recorded original. Preserve `~/.paseo` and all session/worktree data. Removing the npm CLI is a separate explicit decision; never delete user-owned config automatically.

References: [Paseo installation](https://paseo.sh/docs), [connectivity](https://paseo.sh/docs/connectivity), [security](https://paseo.sh/docs/security), [providers](https://paseo.sh/docs/providers).
