# OpenCode v2 — one-way migration handoff

**Baseline:** `main` at `1557ddfcf3cc53dc483533c1eb593509319f6363` (31 October 2026).  
**Status:** First draft PR removes obsolete Quota only; v2 runtime migration remains unimplemented.  
**Author:** ChatGPT (GPT-6); no independent colleague review or live host tests performed.

## Decisions

- One-way v1→v2 migration, no dual v1 renderer and no v1 runtime rollback.
- Install the **latest stable OpenCode v2** at installation/update time and assert the installed major. Do not use an unverified package alias. This requires continuing upstream compatibility maintenance.
- Keep the shared tier registry, orchestrator, ACP integrations, model/variant routing and existing service integrations.
- Keep `opencode-mem@latest`, its `DOTFILES_RUN_OPENCODE_MEMORY_SETUP` opt-in and persisted data, with a private pre-migration snapshot; do not mix v1/v2 writers against a single memory store.
- Remove `opencode-quota` and dotfiles-owned obsolete keys, not user-authored files.
- Fail closed on incompatible Meridian, unsafe permissions, unverified credentials or broken plugin loading rather than silently disabling capabilities.

## Suggested plugin baseline

| Feature | Package / source | Minimum candidate version |
|---|---|---|
| Host | Latest official stable OpenCode v2 | 2.2.0+ (currently 2.2.5) |
| Orchestration | `oh-my-opencode-slim@latest` | 3.0.4+ |
| DCP | `@tarquinen/opencode-dcp@latest` | 3.2.0+ |
| Plannotator | `@plannotator/opencode@latest` | 0.28.22+ source candidate, check npm |
| TokenScope | `@cheesecodestudio/opencode-tokenscope-v2@latest` | 2.0.0+ |
| Secret redaction | `link-seek/opencode-vibeguard` | reviewed Git commit at source 0.2.0+ |
| Voice | `@bojackduy/opencode-voice@latest` | 0.11.1+ source candidate, check npm |
| Memory | `opencode-mem@latest` | 2.29.2+; prefer verified 2.31.x+ |
| Quota | **none** | intentionally removed |
| Meridian | `@rynfar/meridian` via `meridian setup --v2` | newest release qualifying host |

**Version policy:** these are candidate floors, not verified installation results. Confirm npm publication and the actual loaded versions. `@latest` must not be confused with guaranteed cache refresh.

## Meridian version strategy

Upstream `docs/agents.md` currently qualifies OpenCode **2.2.0** on Darwin arm64 and released **2.0.16, 2.0.21, 2.0.31** (plus named betas). It uses a fail-closed `SUPPORTED_OPENCODE_V2_VERSIONS` allowlist. The current latest v2 may be newer than its qualified set.

1. Detect the installed executable's real version and platform; include desktop bundle path where appropriate.
2. Update Meridian and run `meridian setup --v2 --opencode-bin <path>`.
3. For newer hosts, run its stable-v2 integration harness (`scripts/e2e-opencode-v2-stable-live.mjs`) or an equivalent isolated end-to-end suite before proposing upstream support or a reviewed maintained fork.
4. Test `providers.anthropic.settings.baseURL`, request identity headers, actual served models and variants, compaction, subagents, and failures. Meridian's plugin does not set the proxy base URL automatically.
5. Stop deployment on unqualified hosts. Never bypass the host check with a blanket `>=2.0.16` condition.

## Phased PRs

### A. Quota removal and handoff (this draft)

Remove the Quota manifest entry, adjust regression test, and delete stale docs. Preserve `opencode-mem@latest` gate. User-owned Quota sidecar settings remain untouched.

**Acceptance:** canonical manifest/installer/config remain consistent; focused regression and `make verify` pass. **Evidence:** diff inspected; host tests and `make verify` NOT RUN in this environment.

### B. Host installer and v2 configuration

Update Homebrew/Windows install and explicit host-version verification. Migrate the agentic-review workflow's current `opencode-ai@1.18.34` pin and its security tests/docs. Convert generated `opencode.json` keys from v1 to v2 (`provider`→`providers`, `options`→`settings`, `plugin`→`plugins`, `agent`→`agents`, `permission`→`permissions`, `bash`→`shell`, `task`→`subagent`), remove `lsp` and obsolete keys. Explicitly restrict external-directory access: v2 permissions differ and ordered last-match semantics matter. Preserve user-authored/project settings and dry-run/atomic writes. Extend manifest/checker only as much as needed for server and CLI plugins.

**Tests:** tiers, models, variants, local and Meridian provider routes, permission deny/allow cases, invalid JSON, gates, project mode, idempotence, version installer errors.

### C. v2 plugins, TUI and voice

Replace v1 TokenScope and VibeGuard, maintain OMO Slim, DCP, Plannotator and memory. Move voice from shared `tui.json` to v2 `cli.json`, preserve user preferences; avoid duplicate DCP/memory TUI registration. Document voice's v2 STT **immediate submit** limitation. Exercise canary-secret model egress, tool restoration, compaction, redaction during memory capture, native ONNX bindings and copied memory database migration. Do not use repository scanner `@taehwandev/vibeguard` as replacement for transport redaction.

### D. Meridian, service and ACP consumer completion

Integrate `meridian setup --v2` qualification and correct provider settings; test actual requests. Update web launchd, service JSON health endpoint, Caddy, multiplexer random port assumptions, and ACP delegation (Pi, Junie, recursion). Validate locally and in CI. Run `make check-plugin-consistency`, `make check-cli-contract`, `make check-env-coverage`, `make check-hashes`, `make check-mcp-parity`, `make check-slim-invariants`, focused tests, **one canonical `make verify`**, and live provider tests before declaring migration complete.

### E. Optional cross-tool parity, separately reviewable

Consider Pi `@xzzpig/pi-vibeguard >=0.2.1`; verify its actual Pi-fork API and rule schema. Pilot shared `@taehwandev/vibeguard` audit read-only, avoid automatic `setup`/`--fix` on managed files. Junie hooks are not proven transport redaction. Defer shared memory proxy, Sleev, replacing OMO, and other architecture changes.

## Stop conditions and responsibility

Block default deployment on unsupported/latest-non-v2 executable, Meridian misrouting, widened external-directory permissions, canary-secret egress, plugin load failures, wrong provider/model alias, lost memory, user-owned JSON overwrite, false HTTP health check, or CI still pinned to v1. One-way means no dual support, **not** no backups or unsafe state mutation.

Owner-only verification: real model credentials/entitlements and billing, Meridian OAuth, hardware voice, native macOS/Windows installs, production memory migration, live Caddy and ACP paths. Mark untested as **UNKNOWN**, not passed. Work in small draft PRs and follow current `AGENTS.md`/home guidance for authorisation, CI assets, reviewer attribution, and merge.

## References

- [OpenCode v2 migration](https://dev.opencode.ai/v2/docs/migrate-v1/)
- [Meridian integration](https://github.com/rynfar/meridian/blob/main/docs/agents.md#opencode-v2)
- [OpenCode Memory](https://github.com/tickernelz/opencode-mem#-getting-started)
- [OMO Slim compatibility](https://github.com/alvinunreal/oh-my-opencode-slim/blob/master/docs/opencode-v2-compatibility.md)
- [Plannotator](https://github.com/backnotprop/plannotator/blob/main/apps/opencode-plugin/README.md)
- [Voice v2](https://github.com/bojackduy/opencode-voice)
- [TokenScope v2](https://github.com/cheesecodestudio/opencode-tokenscope-v2)
- [VibeGuard candidate](https://github.com/link-seek/opencode-vibeguard)
