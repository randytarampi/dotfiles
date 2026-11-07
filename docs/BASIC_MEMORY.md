# Basic Memory — fleet-wide local MCP memory

**Status (reconciled 2026-11-07):** repository integration delivered in merged [PR #147](https://github.com/randytarampi/dotfiles/pull/147), with follow-on documentation [#148](https://github.com/randytarampi/dotfiles/pull/148). The owner reports Basic Memory working in daily use over recent days (2026-11-07; **owner-reported**, not independently replayed). [Issue #214](https://github.com/randytarampi/dotfiles/issues/214) is now narrowed to telemetry, isolation, offline failure and non-destructive rollback negative cases rather than requiring redundant daily-use proofs. The 2 November 2026 `main@5409ed2669b2` baseline and any original review/test statements below are historical, not current-head CI evidence. See the [modernization roadmap](MODERNIZATION_ROADMAP.md) for current status and deferrals.

## Delivered repository contract and deliberate limitations

- Basic Memory v1.0.0 is the one shared local Markdown knowledge system for tools configured through `configs/mcp/global-mcps.json`. All 15 managed *global* MCP registrations contain `basic-memory`. The per-project MCP registry remains independent to avoid duplicate registration.
- Each supported client starts `uvx --prerelease=allow --from basic-memory==1.0.0 basic-memory mcp` using stdio. It accesses the same user-account data directory (default `~/basic-memory`) and index/config (default `~/.basic-memory`). This is per-user, **per-host**: there is no automatic synchronization between machines.
- A package/model cache warm-up and `uvx` visibility from GUI and CLI client environments are required. The first run downloads Python dependencies, a FastEmbed embedding model and a cross-encoder model; no remote inference endpoint is configured.
- Locally stored Markdown is authoritative; semantic search uses local FastEmbed. The reranker is enabled with `BASIC_MEMORY_RERANKER_ENABLED=true`, `BASIC_MEMORY_RERANKER_PROVIDER=fastembed`, `BASIC_MEMORY_RERANKER_MODEL=jinaai/jina-reranker-v1-tiny-en`. Only vector/hybrid retrieval is reranked. The enabled provider **fails visibly** on invalid config rather than silently skipping reranking.
- Managed MCP process environments set `BASIC_MEMORY_NO_PROMOS=1`, `BASIC_MEMORY_LOGFIRE_ENABLED=false`, `BASIC_MEMORY_LOGFIRE_SEND_TO_LOGFIRE=false`, `OTEL_SDK_DISABLED=true`, `BASIC_MEMORY_FORCE_LOCAL=true` and `BASIC_MEMORY_AUTO_UPDATE=false`. Umami vendor telemetry and promo events are disabled. A manual `bm` command outside this process environment does not inherit these variables unless the user exports them separately.
- The OpenCode v2-specific `opencode-mem` plugin is no longer managed. The global OpenCode v2 configuration emits `-opencode-mem` to disable previously installed package plugins, because merely dropping them from a manifest does not prevent OpenCode from loading its local package cache. Pre-existing `~/.opencode-mem/data` and `~/.config/opencode/opencode-mem.jsonc` remain undisturbed; no automatic data conversion, active dual writer or delete.
- MCP tools provide explicit read/write/context access; they do **not** establish reliable automatic event capture, per-session provenance or per-project worktree scoping across all agents. Native integrations are a separate follow-up.

## Implemented source map

- `configs/mcp/global-mcps.json`: 15 global consumers; the canonical registry continues to own registrations.
- `configs/mcp/basic-memory.json` and `configs/mcp/templates/basic-memory.json`: parity-matched stdio templates, pinned runtime, local reranking and telemetry settings.
- `configs/opencode/opencode-plugins.json`: remove the obsolete OpenCode-only plugin and gate.
- `.chezmoiscripts/run_onchange_15-configure-mcp.sh.tmpl`: hash the new templates to regenerate managed MCP outputs.
- `scripts/lib/tests/test_opencode_plugins.py`, `scripts/lib/tests/test_install_host_gate.py`: retire the old manifest expectation.

## Evidence and required maintainer checks

**Historical source inspection (2 November):** upstream 1.0.0 documentation and CLI source support `basic-memory mcp` and local FastEmbed reranking; `BASIC_MEMORY_NO_PROMOS=1` disables Umami promo telemetry; Logfire is opt-in; the dotfiles central MCP generator supports command templates for all listed clients.

**Delivery evidence:** the repository integration merged as #147. **This documentation reconciliation did not run** the installed host, network-egress, semantic-ranking, cross-client or full local `make verify` checks. Record exact current-head CI separately from owner-host acceptance; do not infer a real memory consumer from merged templates or mock tests. The following historical checklist remains as a useful host-run reference; the *currently required* negative-case subset is tracked in [#214](https://github.com/randytarampi/dotfiles/issues/214), and previously working normal-flow cases need not be rerun without a reason.

1. Confirm pinned `uvx` runtime/CLI and Python 3.12+ availability on macOS, Linux, and supported Windows targets. Validate GUI IDE PATH, model download and offline re-launch.
2. For subsequent source changes, run `make check-mcp-parity`, `make check-plugin-consistency`, focused tests and `make verify` at the exact PR head; on a qualified host, deploy twice and prove idempotence and no user-authored config regression.
3. In an isolated Basic Memory project, save a harmless unique note from Pi, retrieve from a fresh OpenCode v2 and Claude session, and exercise real vector/hybrid search with reranking. Verify per-project isolation, worktree mapping, and the difference between remembered agent notes and current CodeGraph/index state.
4. Verify telemetry off with a blocked outbound network or capture sink and assert no unexpected Umami/Logfire/OTel requests. Check that first-time package/model downloads do not get confused with *telemetry*.
5. Prove that OpenCode Mem user data remains available in a private backup and that a disabled/unavailable `uvx` fails visibly without corrupting memory, bypassing MCP permissions, or rewriting unrelated agent state.
6. For a future migration of existing `opencode-mem` records, first export and inspect the records in a private path, map identities/provenance, deduplicate, and import only with owner approval. Do not publish secret-bearing exports in PR comments.

## Optional LiteLLM-backed reranker — not in default path

Basic Memory supports `BASIC_MEMORY_RERANKER_PROVIDER=litellm` via its own optional `basic-memory[litellm]` extra and a configured reranker model/API. Upstream's extra is currently constrained to LiteLLM SDK `>=1.91.5,<1.92.0`, different from this repo's proxy v1.106.6. Keep environments separate. The proposed `jinaai/jina-reranker-v3-mlx` is an **owner-reported host model**, not verified here as a LiteLLM `/rerank` endpoint or Basic Memory-compatible response. Its availability cannot be inferred from model discovery alone. Do not add dynamic selection until an authenticated live rerank, shape, local-only routing, timeout and failure test succeeds. Keep local FastEmbed default.

## Optional LiteLLM / Open WebUI MCP — deferred for security

Basic Memory v1.0.0 offers `basic-memory mcp --transport streamable-http --host 127.0.0.1 --port 8000` at `/mcp` but **does not authenticate inbound calls**. That endpoint exposes note writes/deletes. The existing Open WebUI reconciler automatically imports HTTP registry entries; a bare localhost HTTP endpoint would be too permissive, particularly with browser, other-user or proxy access. The repo's LiteLLM MCP gateway can become an authenticated front door, but must not expose write/delete privileges indiscriminately, or require every desktop MCP client to depend on LiteLLM's uptime. Implement only after reviewing a scoped auth boundary, the gateway's stdio-server feature gate, credential ownership, and negative access tests. No direct Caddy exposure.

## Rollback and deliberately deferred scope

Rollback consists of removing the Basic Memory registry entries and regenerating client configurations after making backups. **Never delete** `~/basic-memory`, `~/.basic-memory`, `~/.opencode-mem/data` or user-owned MCP files merely to roll back a configuration. Automatic capture hooks, memory-agent inference, cross-host sync, background service, Caddy exposure, automated migrator and dynamic LiteLLM reranker selection remain deliberately deferred, not unchecked criteria for #214. Live acceptance requires explicit issue evidence, not an assumption that the merged setup was exercised.

## References

- [Basic Memory](https://github.com/basicmachines-co/basic-memory)
- [Version 1.0.0](https://github.com/basicmachines-co/basic-memory/releases/tag/v1.0.0)
- [Telemetry](https://github.com/basicmachines-co/basic-memory#telemetry)
- [Local and LiteLLM reranking](https://github.com/basicmachines-co/basic-memory/blob/main/docs/semantic-search.md)
- [Unauthenticated HTTP transport](https://github.com/basicmachines-co/basic-memory/blob/main/src/basic_memory/cli/commands/mcp.py)
