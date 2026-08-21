# Telemetry Opt-Out Dispositions

This document records the telemetry opt-out mechanism for each agent tool in
the dotfiles fleet. The env vars below are set (effective, not commented) in
`dot_dotfiles/shell/.env.example`. For existing installations, `make migrate`
inserts these keys as commented entries in `~/.env`; uncomment them in `~/.env`
to activate.

## Env var approach (primary)

Most tools are controlled via environment variables loaded from `~/.env` at
shell startup. These are the canonical opt-out levers:

| Tool | Env var(s) | Config file | Disposition |
|---|---|---|---|
| claude | `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` | `~/.claude/settings.json` (optional) | env var sufficient |
| codex | `OTEL_SDK_DISABLED=true` | `~/.codex/config.toml` | env var (OTel SDK); config required for complete opt-out — add `[analytics] enabled = false` and `[otel] exporter = "none"` to `~/.codex/config.toml` |
| gemini | `GEMINI_TELEMETRY_ENABLED=false`, `GEMINI_TELEMETRY_LOG_PROMPTS=false` | `~/.gemini/settings.json` (optional) | env var sufficient |
| pi | `PI_TELEMETRY=0`, `PI_SKIP_VERSION_CHECK=1` | `~/.pi/agent/settings.json`: `enableInstallTelemetry: false`, `enableAnalytics: false` | env var + config injection |
| cline | `CLINE_OTEL_TELEMETRY_ENABLED=false`, `OTEL_SDK_DISABLED=true` | VS Code extension settings | env var (OTel SDK) |
| opencode | — | — | no known telemetry collection |
| copilot | — | — | no local opt-out (org/enterprise policy) |
| cursor | — | — | no local opt-out (account dashboard) |
| agy (Antigravity) | — | — | no local opt-out (Google account policy) |
| junie | — | — | IDE setting (not env-manageable) |
| cortex | — | — | no local opt-out (Snowflake account governance) |

Codex note: `OTEL_SDK_DISABLED=true` suppresses the OTel SDK but does not fully
disable Codex analytics. Codex requires `config.toml` settings
(`[analytics] enabled = false` and `[otel] exporter = "none"`) for a complete
opt-out.

## General-purpose env vars

These are not tool-specific and affect multiple tools:

| Env var | Affects | Notes |
|---|---|---|
| `DO_NOT_TRACK=1` | Best-effort convention | Not universally respected; may affect some tools |
| `OPENSPEC_TELEMETRY=0` | OpenSpec | Disables OpenSpec-specific telemetry |
| `OTEL_SDK_DISABLED=true` | All OpenTelemetry-instrumented tools | Affects Codex, Cline, and any OTel consumer; broad scope |
| `CLINE_OTEL_TELEMETRY_ENABLED=false` | Cline | Cline-specific OTel flag |

## Pi config-file injection

Pi is the only tool where telemetry is also disabled via config-file injection
(not just env vars). The `scripts/configure-pi.py` script sets:

```json
{
  "enableInstallTelemetry": false,
  "enableAnalytics": false
}
```

in `~/.pi/agent/settings.json` when `DOTFILES_RUN_PI_SETUP=1`.

## Tools with no env-manageable opt-out

Copilot, Cursor, Antigravity, Junie, and Cortex do not expose an env-var-based
opt-out. Some have local UI settings or account-level controls:

- **Copilot**: GitHub org/enterprise policy (no local opt-out)
- **Cursor**: Cursor account dashboard Privacy Mode (no local opt-out)
- **Antigravity**: Google account/Cloud policies (no local opt-out)
- **Junie**: IDE Settings → System Settings → Data Sharing (local UI setting, not env-manageable)
- **Cortex**: Snowflake account governance (no local opt-out)

## Verification

Run `make check-fleet-coverage` to validate that every tool in the fleet
registry has a documented telemetry disposition and that env vars in
`.env.example` match the registry.
