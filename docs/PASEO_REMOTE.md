# Paseo: private remote coding-agent sessions

**Status:** CLI package managed by the dev-tier Brewfile at npm `@latest`; daemon/network configuration remains an opt-in pilot. Does not enable a public listener, relay, or daemon automatically. Read [REMOTE_ACCESS.md](REMOTE_ACCESS.md) first.

## Owner-run setup

1. Confirm an enrolled Tailscale host and mobile client, with grants limited to the intended daemon port (`6767`). Test OpenCode v2 connectivity independently before installing Paseo.
2. On the development host: `DOTFILES_RUN_PASEO_SETUP=1 bash scripts/install-paseo.sh`. This installs the `@getpaseo/cli@latest` CLI for the current user; review npm scope/PATH first. If Desktop Paseo is already installed, avoid creating a competing daemon. The installer requires a stable semantic version from `paseo --version`; this is a floating channel and must be re-qualified after every version change. Check `paseo --version`, `paseo daemon status` and the process owner.
3. Interactive authentication: `paseo daemon set-password`. Explicitly disable relay with `paseo daemon config set daemon.relay.enabled false` (important for older homes, whose omitted setting may enable relay). Keep the generated bcrypt hash in user-owned `~/.paseo/config.json`; never store the plaintext in the repo, shell history, or a URL query parameter.
4. Run `python3 scripts/configure-paseo-tailnet.py` (read-only). Then `DOTFILES_RUN_PASEO_SETUP=1 python3 scripts/configure-paseo-tailnet.py --apply`. This checks `tailscale ip -4`, requires a structurally valid bcrypt hash and an **explicit** `daemon.relay.enabled: false` setting, refuses a non-default existing listener or an override such as `PASEO_RELAY_ENABLED=true`, and delegates the edit to Paseo's own `daemon config set` command, and verifies no unrelated JSON content changed. Record the original listener and private config backup first.
5. Restart via Paseo's native supervisor (`paseo daemon restart` if already running, otherwise `paseo daemon start`). Test the mobile **direct connection** to the host's Tailscale IP, port 6767, SSL off (Tailscale encrypts transport), with the Paseo password. Do not bind the daemon to `0.0.0.0`.

A stored listen address is not the active socket: foreground `PASEO_LISTEN` overrides and legacy supervisors may retain launch overrides. Check `paseo daemon status` after restart, and require the actual bound IP (not just config JSON) before mobile testing.

### Upgrading from v0.14.0

Paseo v0.15.0 was released on 3 November 2026 and supports reuse of running OpenCode v2 services. The first upgrade **still closes active terminal processes**; only subsequent restarts can preserve supported terminal sessions. Schedule installation when terminal sessions are idle and record the active daemon owner and version before the change. Agent recovery is **off by default**; enabling it is a separate user decision in Host settings → Agents. Do not silently resume coding tasks on deployment.

Before updating, make a permission-restricted backup of `~/.paseo/config.json` outside the repository. If you later downgrade to v0.14.0, restore the original config backup (or remove the new agent-resume and usage-check settings) **before** starting the older daemon. No unattended migrations or config rollback are performed by the installer.

The CLI supports the locally installed provider binaries and their existing authentication/skills/MCP configuration. Prove actual OpenCode v2 and Pi tasks in disposable worktrees, then Claude and Codex. Running `com.opencode.web` does not prove Paseo attaches to that session; the v0.15 beta adds reuse of running OpenCode services but is **not** adopted by this pilot.

## Boundaries and negative tests

- Paseo **CLI installation** is part of the dev-tier `Brewfile.dev` package lane when packages setup is enabled; this follows the owner's `@latest` policy. The separate `install-paseo.sh` helper remains an explicit owner-run action. Paseo **daemon config and Web UI setup** are opt-in via their gates. No unattended session starts or CLI provider reconfiguration.
- Missing/broken Tailscale, unprotected Paseo daemon, existing custom listener, enabled relay, missing CLI or config: refuse instead of widening access.
- The separate Paseo Web UI lane serves its Caddy sites per `CADDY_ACCESS` when enabled and installed. Paseo's native password is the boundary in public mode. It requires a loopback-only Paseo daemon; direct-Tailscale binding is an alternative, not simultaneously served on loopback. No Cloudflare Tunnel, Tailscale Funnel or E2EE relay is enabled here. Direct Tailscale TCP access still requires Paseo's own password.
- Test denial for the wrong tailnet identity and wrong app password. Test session continuity after app suspension and after switching between Wi-Fi and cellular; confirm terminal, diff and approval surfaces.

## Bundled Web UI through Caddy

Set a Paseo daemon password with `paseo daemon set-password` and explicitly
disable relay in native configuration. Set `DOTFILES_PASEO_RELAY=0` (the default);
a hosted relay and a Caddy Web UI route are alternative exposure paths, not
simultaneously supported. An explicit owner-only `configure-paseo-web.py --apply`
can reconcile relay drift before Caddy is configured, but Caddy refuses a
remaining active relay or process-level `PASEO_RELAY_ENABLED` override.
With a loopback daemon, set
`DOTFILES_RUN_PASEO_WEB_SETUP=1` and `DOTFILES_RUN_CADDY_SETUP=1`.
`make deploy` performs read-only native Web UI inspection; it does not
apply native Paseo configuration changes. Inspect with `make paseo-web-check`,
then make a private backup, ensure the daemon and other Paseo settings writers
are quiescent, and explicitly run `make paseo-web-apply` before redeploying.
This operator procedure is not a cross-process transaction (issue #173).
`CADDY_ACCESS=localhost` serves `https://paseo.localhost`; `lan` serves
configured `paseo.<domain>` hosts to private ranges; `public` serves them
publicly. A gate-enabled, installed service follows `CADDY_ACCESS`; the Paseo
native password is the boundary in public mode. The bcrypt password is
required for LAN and public access. The native daemon remains loopback-only,
and its hostname allowlist is extended through validated CLI writes and fails
closed for unlisted hosts. Caddy terminates TLS and Paseo authenticates API
and WebSocket clients.

The script preflights edits in a private home and does not automatically
install or start Paseo. Native config writes are not a multi-writer transaction
(see issue #173): never run concurrent Paseo UI, daemon, or CLI config writers.
A daemon listening on a direct-Tailscale IP is not simultaneously available on
127.0.0.1; treat direct tailnet binding as an alternative topology.

## Rollback

Stop the user-owned Paseo daemon with `paseo daemon stop`. Restore the prior user-controlled `daemon.listen` through `paseo daemon config set daemon.listen 127.0.0.1:6767`, only if that was its previous state; otherwise restore the recorded original. Preserve `~/.paseo` and all session/worktree data. Removing the npm CLI is a separate explicit decision; never delete user-owned config automatically.

References: [Paseo installation](https://paseo.sh/docs), [connectivity](https://paseo.sh/docs/connectivity), [security](https://paseo.sh/docs/security), [providers](https://paseo.sh/docs/providers).
