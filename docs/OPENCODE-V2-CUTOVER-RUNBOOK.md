# OpenCode v2 one-way cutover runbook

This is an owner-run procedure. The live macOS cutover reported below is **owner-reported evidence**, not independently reproduced verification of every consumer in steps 9–11. The detailed local deepwork runbook is the origin of this sanitised procedure; consult that private artifact for machine-specific execution notes. Do not copy secrets, private paths or user data into this document or review systems.

## Preflight and hold point

1. Record candidate commit IDs, CI/security status, resolved findings, target OS, installed OpenCode provenance/version, package-manager ownership, Meridian version/registration/gates, upstream stable version and available plugins. Inventory effective routes and health endpoints without recording secret values.
2. Create a verified private snapshot of OpenCode configuration and user-authored CLI/TUI files, memory data, Meridian state, web service configuration, relevant generated files and non-secret install metadata. Restore a copy in isolation and verify it before proceeding. This is recovery protection, not an automatic v1 rollback plan.
3. Decide the Meridian path. For the selected persistent-registration sequence, install a CLI version actually qualified for CLI use (2.0.31), then prove registration persistence and latest-host runtime with a real routed request. If Meridian is essential and this cannot be established, stop before changing the live host.
4. Confirm the integrated candidate contains the official installer, PATH precedence and negative install tests; account for legacy package conflicts. Use one writer and no parallel service/config mutators.

## Execution and proof

5. Install the OpenCode CLI to the qualified release: `scripts/install-opencode-v2.sh --version 2.0.31`; resolve the absolute binary path and verify exact version in login shell, non-login execution and service context.
6. Run `meridian setup --v2 --opencode-bin <resolved-path>` exactly once. Confirm plugin registration (the generator now preserves this entry across regenerations), then refresh to the latest stable v2 with `scripts/install-opencode-v2.sh --install-latest` and exercise a real correctly routed Meridian request on that latest host. Leave `DOTFILES_RUN_MERIDIAN_SETUP=0` afterwards: deploys keep the registered entry without re-running qualification.
7. Set `DOTFILES_RUN_MERIDIAN_SETUP=0` in the owner environment after successful one-time registration. This is required so the fail-closed first-time qualification gate does not block later deployments; verify the subsequent deploy still preserves and uses the registration.
8. Run the generation chain and managed configuration gates. Check native v2 configuration, permissions, MCP state, voice CLI config, DCP, memory opt-in, plugin load/version convergence and user-file preservation.
9. Re-bootstrap the launchd service with the new absolute binary. Verify authenticated loopback `/api/info` returns the expected version; then verify the external Caddy route, authentication, WebSocket behaviour and unrelated service health.
10. Exercise one real request from each required consumer, including CLI/orchestrator, Meridian and LiteLLM routes, ACP delegates, Plannotator, DCP, TokenScope, voice and copied-memory retrieval/capture. Verify synthetic redaction at provider egress and restoration for local tools.
11. Deploy a second time. Confirm idempotency, no duplicate plugins, no stale v1 service binary, persistent Meridian registration and unchanged auth/permissions. Run focused checks plus canonical `make verify` on the final candidate and record each boundary as VERIFIED, OWNER-REPORTED, NOT RUN, UNKNOWN or N/A.

## Live cutover record (2026-11-02, macOS arm64)

The machine owner reports a macOS arm64 cutover with the qualified CLI, Meridian `--v2` registration, upgrade to OpenCode 2.2.5, launchd re-bootstrap and authenticated loopback `/api/info`, external Caddy Basic auth, two idempotent deploys and `make verify` (exit 0). This does not establish that every consumer listed in step 10 was exercised. Recovery flows and context sensitivities reported from that host:

- **Stale `@latest` package artifacts:** `plugin check` prints a versionless `(check failed)` line when the plugin cache holds a broken or outdated artifact, and the deploy fails closed — by design. Heal the cache with `opencode plugin update <spec>` and re-check; never loosen the parser to trust versionless lines.
- **Instrumented parent shells:** the owner observed an approximately 3 s `opencode --version` under an inherited OTLP exporter endpoint, exceeding Meridian's 3 s probe. Confirm the timing with the managed binary in a clean shell. For the affected diagnostic/deploy child process only, remove inherited `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_PROTOCOL` and `OTEL_METRIC_EXPORT_INTERVAL` as needed; retain `OTEL_SDK_DISABLED` if it is set intentionally. Do not disable telemetry policy globally to address a probe timeout.
- **PATH context:** on the owner's macOS host, the installer updated `~/.zshrc`, so standalone shell invocations may require an absolute managed path or a local PATH adjustment. The plugin installer already resolves `~/.opencode/bin/opencode` via `scripts/lib/opencode_bin.sh`, and the host probe and LaunchAgent likewise prefer managed paths; these consumers do not require adding PATH exports to `~/.zshenv`. Compare `command -v opencode` with the managed binary before assuming shell selection and deploy selection are identical.
- **Caddy serving lane:** the owner observed the repository-managed root LaunchDaemon as the serving instance; a second brew-managed service could conflict over the admin listener (observed at `:2019`). Check the active process/launchd label before making service changes. Prefer the repository's `make caddy-reload` (and Caddyfile validation) to a raw admin-API `POST /load`; use `make caddy-deploy`/`make caddy-restart` only when the lifecycle requires it. See [Caddy operation](CADDY.md). Do not POST arbitrary configuration or start a parallel Caddy instance.

## Windows lane

KNOWN-UNVALIDATED. The selected channel is the official desktop NSIS installer, x64 or aarch64 as appropriate, run silently with `/S`. The exact installed CLI destination and successful version probe must be established on the target before Windows can be declared accepted.

## Stop, containment and recovery

- NO-GO for missing/wrong v2 binary, required Meridian route unqualified or failing, secret egress, lost memory, widened permissions, incorrect Caddy authentication, false health, plugin failures or inconsistent second deploy.
- Stop or disable the affected exposure/consumer, preserve diagnostics and the verified snapshot, and fix forward. Do not silently restore v1, destroy originals, rotate credentials without cause or claim usability while a critical route is broken.
- Mark unperformed host-specific checks UNKNOWN with an accountable owner and follow-up. No fabricated evidence or secret-bearing logs in reports.

Known residual on the plugin checks: the version-check parser matches the binary's printed plugin name against the spec-derived forms (scoped, unscoped and Git repo-derived). The Git-source name mapping derives from the repo name; if the real binary prints a different resolved package identity for the `github:AxeForce/opencode-vibeguard-v2#main` spec, the check fails closed with the printed line in the log — record that line and extend the mapping from the resolved identity; never loosen the exactly-one-match rule.
