# OpenCode v2 one-way cutover runbook

This is an owner-run procedure, not a claim that a live cutover has been tested. The detailed local deepwork runbook is the origin of this sanitised procedure; consult that private artifact for machine-specific execution notes. Do not copy secrets, private paths or user data into this document or review systems.

## Preflight and hold point

1. Record candidate commit IDs, CI/security status, resolved findings, target OS, installed OpenCode provenance/version, package-manager ownership, Meridian version/registration/gates, upstream stable version and available plugins. Inventory effective routes and health endpoints without recording secret values.
2. Create a verified private snapshot of OpenCode configuration and user-authored CLI/TUI files, memory data, Meridian state, web service configuration, relevant generated files and non-secret install metadata. Restore a copy in isolation and verify it before proceeding. This is recovery protection, not an automatic v1 rollback plan.
3. Decide the Meridian path. For the selected persistent-registration sequence, install a CLI version actually qualified for CLI use (2.0.31), then prove registration persistence and latest-host runtime with a real routed request. If Meridian is essential and this cannot be established, stop before changing the live host.
4. Confirm the integrated candidate contains the official installer, PATH precedence and negative install tests; account for legacy package conflicts. Use one writer and no parallel service/config mutators.

## Execution and proof

5. Install the OpenCode CLI with the official script using `--version 2.0.31`; resolve the absolute binary path and verify exact version in login shell, non-login execution and service context.
6. Run `meridian setup --v2 --opencode-bin <resolved-path>` exactly once. Confirm plugin registration, then update OpenCode to the latest stable v2 and exercise a real correctly routed Meridian request on that host.
7. Set `DOTFILES_RUN_MERIDIAN_SETUP=0` in the owner environment after successful one-time registration. This is required so the fail-closed first-time qualification gate does not block later deployments; verify the subsequent deploy still preserves and uses the registration.
8. Run the generation chain and managed configuration gates. Check native v2 configuration, permissions, MCP state, voice CLI config, DCP, memory opt-in, plugin load/version convergence and user-file preservation.
9. Re-bootstrap the launchd service with the new absolute binary. Verify authenticated loopback `/api/info` returns the expected version; then verify the external Caddy route, authentication, WebSocket behaviour and unrelated service health.
10. Exercise one real request from each required consumer, including CLI/orchestrator, Meridian and LiteLLM routes, ACP delegates, Plannotator, DCP, TokenScope, voice and copied-memory retrieval/capture. Verify synthetic redaction at provider egress and restoration for local tools.
11. Deploy a second time. Confirm idempotency, no duplicate plugins, no stale v1 service binary, persistent Meridian registration and unchanged auth/permissions. Run focused checks plus canonical `make verify` on the final candidate and record each boundary as VERIFIED, OWNER-REPORTED, NOT RUN, UNKNOWN or N/A.

## Windows lane

KNOWN-UNVALIDATED. The selected channel is the official desktop NSIS installer, x64 or aarch64 as appropriate, run silently with `/S`. The exact installed CLI destination and successful version probe must be established on the target before Windows can be declared accepted.

## Stop, containment and recovery

- NO-GO for missing/wrong v2 binary, required Meridian route unqualified or failing, secret egress, lost memory, widened permissions, incorrect Caddy authentication, false health, plugin failures or inconsistent second deploy.
- Stop or disable the affected exposure/consumer, preserve diagnostics and the verified snapshot, and fix forward. Do not silently restore v1, destroy originals, rotate credentials without cause or claim usability while a critical route is broken.
- Mark unperformed host-specific checks UNKNOWN with an accountable owner and follow-up. No fabricated evidence or secret-bearing logs in reports.
