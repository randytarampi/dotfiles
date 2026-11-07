# Private remote developer access

**Status:** opt-in host configuration; no public listeners or machine enrolment managed by this repository.

## Intent and ownership

Tailscale owns encrypted device connectivity and tailnet access grants. Existing Caddy localhost/LAN/public modes retain their current meanings. OpenCode v2 owns its server password and listens on loopback. Tailscale Serve may publish **only that explicitly chosen OpenCode service** to authorised tailnet identities. Paseo and Hermes integration are separate stack PRs.

Do **not** expand Caddy `remote_ip private_ranges` to include `100.64.0.0/10`: membership in that address range is not an identity control. Do not use `tailscale funnel` for agent-control services. No LiteLLM, Meridian, oMLX, Ollama, JetBrains MCP or CodeGraph publishing is authorised by this feature.

## Owner-run setup

1. Install Tailscale (the optional macOS `Brewfile.desktop.cloud` already lists `tailscale-app`); enrol the host and iPhone/iPad with the owner-controlled tailnet. Confirm the Tailscale CLI is accessible on the host. Configure grants/ACLs for only approved devices/identities; approve the host and enable MagicDNS/HTTPS certificates where required.
2. Start the existing loopback `com.opencode.web` service, verify OpenCode **v2** and authenticated `GET http://127.0.0.1:${OPENCODE_SERVER_PORT:-4096}/api/info`. `OPENCODE_PASSWORD` or `OPENCODE_SERVER_PASSWORD` must be exported on the operator shell; do not print it or pass it in command arguments. **Apply now verifies** unauthenticated `/api/info` returns HTTP 401, authenticated `/api/info` succeeds, and the JSON version is stable 2.x. Read-only check deliberately does not touch the running OpenCode service.
3. Check for existing Tailscale Serve ownership: `tailscale serve status --json`. Do not overwrite unknown port mappings. Run `python3 scripts/configure-tailnet-opencode.py` (read-only check).
4. Explicitly opt in using `DOTFILES_RUN_TAILSCALE_ACCESS_SETUP=1 python3 scripts/configure-tailnet-opencode.py --apply`. This publishes OpenCode on HTTPS port `8443` **inside the tailnet** only. It refuses conflicting mapping state **including an existing Funnel permission** for the same HTTPS port and does not alter Caddy or DDNS. After application the script reads the Serve status again; this proves only the local Serve mapping, not a remote mobile connection.
5. From an enrolled mobile device on cellular, test the HTTPS URL reported by Tailscale, HTTP authentication, an interactive session and WebSocket reconnect. Separately test a denied tailnet identity. A local check or generated route is not proof of mobile access.

The script intentionally does **not** enrol devices, set grants, provision secrets, install a daemon, or run automatically on `make deploy`. The port can be changed using `--https-port` after inspecting existing mappings. The host's Tailscale Serve JSON schema and permissions must be verified live; unknown shapes fail closed. Tailscale Serve may need tailnet HTTPS certificate approval; use the installed CLI's own setup guidance and do not switch to Funnel to bypass that requirement.

## Rollback and evidence

Inspect `tailscale serve status` and the current configuration first. To remove the *owned* route, explicitly turn off its exact HTTPS port via the installed Tailscale CLI (e.g. `tailscale serve --https=8443 off` after confirming CLI semantics). Never run blanket `tailscale serve reset`: other applications may own mappings. The Caddy config and host model services should remain untouched.

Maintainer evidence: source host/CLI version, enrolled/denied identities (redacted), OpenCode v2 version, successful authenticated `/api/info`, mobile streaming/reconnect, failed unauthorised access, and localhost/LAN regression check. Document anything not exercised as **UNKNOWN**.

References: [Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve), [OpenCode v2](OPENCODE.md), [Caddy](CADDY.md).
### Safety notes (review remediation)

The read-only check and the pre-apply check reject populated service-level and foreground Serve configurations rather than guessing ownership. Tailscale's JSON omits empty `AllowFunnel` fields, so absence of that field alone is not evidence of a public route. The script re-reads Serve configuration immediately before mutation; the native CLI performs its own ETag-aware update, but an external process could still change the route between those two operations. Do not run concurrent Serve writers during owner-approved setup. Use `--dry-run` for a no-write preview.
