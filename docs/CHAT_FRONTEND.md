# Unified Multi-Provider Chat Frontend (Proposed)

> **Status: proposed, not implemented.** This document is the research-backed design
> for a unified chat frontend over the repo's local engines and cloud providers.
> Implementation (Phase 4) is pending approval; nothing here is wired up yet.

## TL;DR

**Recommendation: Open WebUI, adopted in stages**, integrated the same way every
optional service in this repo is integrated — a gate, a generated LaunchAgent, a
Caddy route, and local-provider connections generated from the existing
`LOCAL_ENGINES` registry (cloud providers connect via one-time named
integrations). It is the best ecosystem fit for the requested breadth, but
**no candidate today satisfies breadth, maturity, simplicity and strong
isolation simultaneously**, so adoption is staged and reversible:

1. **Core multi-provider chat pilot** — the only piece recommended for the
   initial build.
2. **Open Terminal** — an additional privileged runtime in the same ecosystem;
   separate security/reliability pilot before it is enabled anywhere.
3. **Open WebUI Computer (`cptr`)** — a separately installed and operated
   application, not a feature of the chat UI; its own experimental decision.

- The licence is non-OSI since v0.6.6 ([licence](https://docs.openwebui.com/license/),
  [licence history](https://docs.openwebui.com/license/history/)) but fine for a
  personal deployment (≤50-user exemption; keep branding).
- The unified-frontend goal itself is **not an anti-pattern** — it is the
  well-established "personal AI gateway" pattern — as long as it is scoped to
  **API-accessible models**. Consumer subscriptions (ChatGPT Plus, Claude Pro)
  are *not* APIs and must stay in their vendor apps.
- The runner-up is **LibreChat** (MIT, lighter, strong agents/MCP) — the
  documented fallback if the core chat pilot shows Open WebUI's breadth or
  release velocity undermining intuitiveness and reliability.
- **egoist/waku is not a chat frontend** — it is a native coding-agent cockpit
  (Claude Code, OpenCode, Pi, …); wrong category, but worth tracking separately
  for the agent-fleet side.

## Requirements recap

- One chat interface across Ollama, oMLX, OpenAI, Anthropic (and OpenRouter, Google…)
- Manage/call agentic tooling sessions where feasible
- Clean plug/unplug of providers and tools — no hacks
- Intuitive to understand and extend
- Documentation + maintenance guidance (new providers, model preset refresh)
- Must actually work well

## Research summary (2026-09-24)

Three parallel research lanes (Open WebUI ecosystem; alternatives landscape;
gateway/abstraction patterns). Sources tagged to retrieval date; primary sources
preferred over marketing pages.

### Is a unified frontend a common want? Yes — and it has a name

The converging community pattern is the **personal AI gateway**:

```text
web UI (chat, history, files, tools)
        │  one OpenAI-compatible endpoint
        ▼
optional local gateway (LiteLLM)     ← only when needed
        │
        ├── Ollama (11434)
        ├── oMLX OpenAI-compatible /v1
        ├── OpenAI API
        ├── Anthropic API
        └── OpenRouter / Google / …
```

- OpenAI-compatible Chat Completions is the lingua franca; Anthropic
  `/v1/messages` is the strong second protocol. Winning designs are dual-protocol.
- The pattern is **not an anti-pattern** when the objective is *one history and
  one control surface for API-accessible models*.
- It **is** an anti-pattern when the objective is one interface that transparently
  inherits every vendor's consumer subscription, private context, native tool
  ecosystem, and quota. **ChatGPT Plus ≠ OpenAI API credit; Claude Pro ≠ Anthropic
  API credit.** Vendor apps stay vendor apps; browser-session/cookie proxies are
  brittle, account-risky, and violate Anthropic's consumer terms (effective
  2025-10-08, which explicitly prohibit automated access and credential sharing).
- Documented failure modes to respect: provider semantics are not uniform despite
  a shared request shape (tool schemas, streaming, vision, reasoning controls,
  caching, context limits); context does not transfer between vendor apps; every
  extra gateway adds latency, an outage domain, and a translation-loss risk.

### Candidate verdicts

| Candidate | Verdict for this repo | Notes |
|-----------|----------------------|-------|
| **Open WebUI** | **Recommended (staged)** | Very active (v0.11.4, 2026-09-21). Native Ollama + arbitrary OpenAI-compatible endpoints + named OpenAI/Anthropic integrations. MCP via Streamable HTTP (admin-configured). Ecosystem also spans Open Terminal (privileged runtime) and Computer (separate app) — adopted in stages. Non-OSI licence since v0.6.6 (fine ≤50 users; keep branding). Fast-moving: pin immutable versioned releases; moving tags like `main`/`main-slim` are not pins. |
| **LibreChat** | Runner-up | MIT, web-first, cleanest add/remove custom-endpoint semantics, first-class MCP + agents, LiteLLM optional. Lighter than Open WebUI; no Open Terminal/Computer equivalent. |
| **AnythingLLM** | Situational | Best if RAG/document-workspaces/Agent-Flows are the priority. Heavier than needed for plain chat; `DISABLE_TELEMETRY=true` required. Documents **oMLX** as a named local provider. |
| **Jan** | Complementary only | Desktop-first (Tauri); weak as a Caddy-exposed web frontend. Its OpenAI-compatible local server (`localhost:1337`, incl. an `mlx-server`) could serve as a local *provider*, not the UI. Apache 2.0. |
| **LobeHub** | Watch | Most active agent-operator platform (Apache 2.0); direction outgrew "chat switcher"; more product complexity than wanted. |
| **egoist/waku** | Excluded (wrong category) | Native coding-agent cockpit for Amp/Claude Code/Codex/Cursor/OpenCode/Pi via per-agent native protocols; young (created 2026-07-31, GPL-3.0-only, active). No Ollama/provider catalogue, no generic OpenAI-compatible endpoint model. Track separately for the agent fleet. |

### What about Open WebUI's own `/alternatives/` pages?

First-party marketing, not neutral evaluations — unusually positive toward
competitors, consistently positioning Open WebUI as the broadest platform. The
competitor facts they state (LibreChat = focused multi-provider chat, MIT;
AnythingLLM = workspace-centred document Q&A; Jan = local-first desktop, Apache
2.0) check out against each project's own docs, so the pages are usable as
orientation but not as benchmarks. Their own feature counts drift between pages
(9 vs 13 vector databases) — treat claims as snapshots.

### Agentic sessions from a web UI — honest maturity read

- Open Terminal ([docs](https://docs.openwebui.com/ecosystem/open-terminal)):
  real, stateful workspaces (files/shell/packages/processes) — runs in Docker or
  on host; treat as **privileged infrastructure**, not a hardened boundary. The
  isolated multi-user "Terminals" product is a separate, enterprise-gated
  offering — not the community Open Terminal runtime discussed here.
- Open WebUI Computer (`cptr`,
  [docs](https://docs.openwebui.com/ecosystem/computer/)): real but explicitly
  "cutting-edge" — signed-in users get keyboard-equivalent power over the
  machine; docs compare exposure to SSH and recommend private-network access.
  Treat as experimental.
- LibreChat workspaces: labelled "highly experimental" by the project itself.
- Industry-wide, a self-hosted web UI that reliably manages persistent arbitrary
  CLI agent sessions (resumability, multiplexing, permissions, audit) does not
  exist yet as an interoperable standard.

**Implication:** the "manage multiple agentic tooling sessions" itch is better
served by this repo's existing fleet (11 CLI agents + OpenCode web on :4096)
than by bolting agent orchestration onto a chat UI in 2026. Open WebUI Computer
is worth adopting as an *opt-in* experiment when we get to it — it is genuinely
useful (git worktrees, diffs, terminals per project) but is a separate
application/runtime with its own access boundary, not a toggle inside the chat
frontend.

## Recommended architecture

```text
                         ┌─────────────────────────────────┐
   browser (LAN) ──► Caddy ──► Open WebUI (LaunchAgent, :8080)
                         │        │  local connections generated from
                         │        │  LOCAL_ENGINES; cloud providers as
                         └────────┼─────────────────────────────────┘
                                  │      one-time named integrations
        ┌────────────┬───────────┴──────────┬──────────────┐
        ▼            ▼                      ▼              ▼
     Ollama        oMLX               OpenAI/Anthropic   OpenRouter
     :11434   :OPENWEBUI_…/v1          (named integrations/   (optional)
   (native)    OpenAI+Anthropic API    direct API keys)
```

### Design principles (mapped to repo patterns)

1. **`LOCAL_ENGINES` is the source of truth for *local* connection metadata.**
   Local-provider connections are *generated*, never hand-entered in the admin
   UI. The configure script emits
   `OPENAI_API_BASE_URLS` / `OPENAI_API_KEYS` / `OPENAI_API_CONFIGS` and
   `OLLAMA_BASE_URLS` (semicolon-separated, parallel order — verified against
   the [Open WebUI env-configuration reference](https://docs.openwebui.com/reference/env-configuration/),
   2026-09-24) from `active_engines()` + `local_endpoint_for()`.
   **Config ownership model (explicit):** these are Open WebUI `ConfigVar`s —
   under the default `ENABLE_PERSISTENT_CONFIG=True`, database values take
   precedence over environment variables after first launch, so hand-entered
   admin-UI changes silently win over regeneration. The initial build sets
   `ENABLE_PERSISTENT_CONFIG=False` (environment-authoritative): managed
   connection settings regenerate from `~/.env` + the registry on every
   restart, admin-UI connection edits become ephemeral, and runtime
   conveniences that are not connection settings still persist. The
   alternative (database-authoritative with idempotent API reconciliation)
   is documented as the fallback if ephemeral admin settings prove
   unacceptable in practice. `OPENAI_API_CONFIGS` / `OLLAMA_API_CONFIGS`
   supply per-connection `prefix_id` (stable model-ID prefixes) and
   `enable`/`connection_type` so duplicate model IDs across Ollama, oMLX and
   OpenAI-protocol clouds stay unambiguous.
   Plug/unplug a *gated* engine = flip its `DOTFILES_RUN_*` gate + `make
   deploy`; **Ollama is the exception — it has no gate** (`LOCAL_ENGINES`
   entry `gate_env: None`), so it is treated as always-present and its entry
   is deduplicated out of the OpenAI-protocol array (Ollama connects via the
   native `OLLAMA_BASE_URLS` path only, not also as an OpenAI-protocol
   endpoint — the dedup choice is resolved here, not left open). The same
   registry already feeds OpenCode providers, Mozart, Caddy, and Junie — this
   adds one more consumer, not a new source of truth.
   **Offline engines:** `active_engines()` checks gates, not reachability. An
   enabled-but-offline engine keeps its generated connection; Open WebUI shows
   an empty model list for it until the endpoint answers. If empty-list
   behaviour is intrusive in practice, the `*_API_CONFIGS` `enable` flag is
   the documented toggle.
   **Cloud providers are NOT generated.** Open WebUI exposes no env-var path
   for Anthropic-type connections, and its OpenAI-integration env vars are
   `ConfigVar`s that conflict with the environment-authoritative mode above;
   cloud connections (OpenAI, Anthropic, OpenRouter, Google) are therefore
   one-time named integrations configured in the admin UI with API keys from
   `~/.env`. `LOCAL_ENGINES` is authoritative for local endpoint metadata
   only — not for all Open WebUI state.
2. **Gate + LaunchAgent + Caddy, like every optional service.**
   A new `DOTFILES_RUN_*_SETUP` gate (default 0; concrete name chosen at
   implementation — see open questions), documented in `.env.example`
   (check-env-coverage), Layer-2 script `run_onchange_30-openwebui.sh.tmpl`
   (next free slot; hash-triggered on the configure script), LaunchAgent
   `com.openwebui.web` (mirrors `com.opencode.web`), Caddy site on a
   **dedicated host/subdomain** (e.g. `chat.<domain>`) behind the existing LAN
   allowlist + basic auth. A dedicated host is the default because a path
   prefix (`/webui/*`) can break root-relative assets, redirects and
   WebSocket URLs; subpath serving would need local verification first and
   is not assumed.
3. **Cloud providers via one-time named integrations.** OpenAI/Anthropic/
   OpenRouter/Google connect with their own API keys from `~/.env`
   (upstream-native env vars; no new `DOTFILES_` names for provider keys),
   configured once in Settings → Admin → Connections. Meridian
   (Anthropic-compatible `:3456/v1`) **remains excluded by default**: it is
   OAuth-backed (Claude Code SDK), not an API-key service, so exposing it as a
   general chat API would be exactly the subscription-to-API bridge this
   design rejects. Inclusion would require *both* technical compatibility
   (custom base URL on an Anthropic connection) *and* an explicit decision
   that this particular OAuth-backed use is authorized — otherwise Meridian
   stays an OpenCode/Mozart-only path and chat uses the normal Anthropic API
   integration (no hacks).
4. **No subscription proxying. Ever.** ChatGPT Plus / Claude Pro sessions are not
   API backends; they stay in their vendor apps.
5. **Security boundaries stay separate.** The chat frontend is the only
   Caddy-exposed piece: LAN-accessible via the repo's LAN allowlist + basic
   auth, and **never inheriting a global `CADDY_ACCESS=public` mode** — if the
   Caddy exposure mode changes, the chat route's exposure must be reviewed
   explicitly. Application-level auth is its own layer (Caddy basic auth is an
   outer shell, not a substitute): initial admin bootstrap via
   `WEBUI_ADMIN_EMAIL`/`WEBUI_ADMIN_PASSWORD` (auto-disables signup after
   creating the admin), `ENABLE_SIGNUP=False` thereafter, single-user posture.
   Open Terminal and Computer start **disabled and unregistered** from the
   LAN-facing instance; when adopted, they get their own access decision —
   Open Terminal as a separate localhost-only runtime, Computer as a separately
   installed application with its own auth — never implicitly reachable
   through the Caddy-exposed chat instance. "Localhost-only" (loopback) and
   "private network" (Tailscale/LAN) are distinct exposure modes and never
   conflated.
6. **No LiteLLM gateway initially.** oMLX already speaks OpenAI + Anthropic and
   Ollama is built in; both UIs connect directly. LiteLLM becomes a documented
   escalation if provider count, aliasing, fallbacks, budgets, or per-client keys
   grow — it is *not* part of the initial build (avoids over-layering; Mozart
   already exists for the OpenCode side).
7. **Pin immutable releases.** Open WebUI moves fast; the LaunchAgent installs
   an immutable versioned release — exact pip version or a digest-pinned
   image, updated deliberately. Moving tags (`main`, `main-slim`) are *not*
   pins and are not used.

### What Open WebUI gives us beyond chat (staged, opt-in)

- Model selector across all configured connections; multi-model chats
- Workspace models (wrap a base model with instructions/tools/knowledge)
- MCP tool servers (Streamable HTTP, admin-configured) — pairs with the repo's
  MCP registry; stdio/SSE servers need the upstream `mcpo` bridge (document as a
  limitation, not a workaround we build)
- RAG/knowledge bases (hybrid BM25 + vector), web search, scheduled automations
- Open Terminal — separate security/reliability pilot, disabled by default
- Open WebUI Computer — separately installed and operated application, own
  experimental decision with its own access boundary

### Operations snapshot (from upstream docs, Sept 2026)

- Default SQLite + local ChromaDB is fine for one user; **not** for network
  filesystems or multi-worker. Postgres/Redis only if scaling later.
- Install via pip-in-venv (exact pinned version) or digest-pinned image; the
  slim image family (~175 MB, omits local embeddings/speech/rerank/extraction)
  is right for cloud/local-API-only use. Personal footprint is plausibly
  2–4 GB RAM without local RAG/embedding workloads — **unvalidated planning
  estimate**, not an official minimum; treat every sizing figure as an
  estimate until measured.
- Set a persistent `WEBUI_SECRET_KEY` (in `~/.env`; required for stable
  sessions/OAuth token decryption across restarts).
- Model lists are discovered live through each connection's **native
  model-list mechanism** (Ollama via its native API, OpenAI-protocol
  connections via `/v1/models`); no checked-in chat catalogue exists, unlike
  OpenCode's catalogs. Admin pin/hide/order preferences are persisted Open
  WebUI state — under the environment-authoritative policy above they remain
  persistent (they are not connection settings), but their interaction with
  regeneration must be validated during implementation.
  Documented refresh flow: restart the service after changing engine gates.

## Maintenance guidance

### Adding a new local engine (e.g. a future local server)

1. Add an entry to `LOCAL_ENGINES` (`scripts/lib/local_engines.py`) with
   `gate_env` (or explicit no-gate, like Ollama — document which and why),
   `api` (must include an OpenAI-compatible protocol), base URL,
   `health_check`, and `caddy_route` if LAN-exposed.
2. The chat-frontend configure script picks it up automatically (it consumes
   `active_engines()` like every other consumer). Document the resulting
   model-ID prefixing (`prefix_id` in `*_API_CONFIGS`) and the
   enabled-but-offline behaviour (connection stays configured, empty model
   list until the endpoint answers).
3. Document the gate in `.env.example`; run `make verify`.

### Adding a cloud provider

Cloud connections are **client configuration, not repo-generated
infrastructure** — Open WebUI has no env-var path for Anthropic-type
connections, and cloud integrations are one-time admin-UI entries using keys
from `~/.env`. No `LOCAL_ENGINES` or gateway change is involved. Note the
boundary: `scripts/lib/provider_endpoints.py` is the OpenCode-side provider
registry (Google, OpenRouter, OpenCode entries with `api` protocols and
allowlist files) — it is **not** a generic all-provider registry and does not
currently cover OpenAI or Anthropic. Extending it is a deliberate decision
that carries OpenCode catalogue and `MODEL_UPDATES.md` obligations; do not
assume adding a chat-frontend cloud provider touches it.

1. One-time named integration in Open WebUI (Settings → Admin → Connections)
   with the provider's API key from `~/.env`.
2. Document any new env var in `.env.example`.

### Model preset refresh

- **Local engines:** models are discovered live through each connection's
  native model-list mechanism (Ollama native API; `/v1/models` for
  OpenAI-protocol connections). Nothing to check in; add/remove a model in
  Ollama/oMLX and it appears. The existing `check-model-drift` gate continues
  to cover OpenCode catalogs only.
- **OpenCode tier presets are unchanged** — this system is a parallel consumer of
  the same engines, not a replacement for the slim.json tier registry.
- Update pinned Open WebUI releases deliberately (bump the pinned version in
  the LaunchAgent template; check release notes for security fixes first).
  Release updates must account for data migration: back up the Open WebUI
  data directory before upgrading, verify the pinned release's migration
  notes, and keep the previous version available for rollback (SQLite data
  is not always backward-compatible across versions).

### Documentation duties when touching this area

- Update this doc for architecture changes; keep `docs-drift` green (link from
  `AGENTS.md`/`README.md`).
- New gates → `.env.example` + `docs/ORCHESTRATION.md`.
- New Caddy route → `docs/CADDY.md` + `scripts/configure-caddy.py`.

## Explicit non-goals

- No subscription-proxying of ChatGPT Plus/Claude Pro (ToS + fragility).
- No Meridian-as-chat-backend unless both the technical and authorization
  conditions in the design principles pass.
- No LiteLLM/OpenRouter gateway layer in the initial build.
- No Open Terminal or Computer exposure — enabled or not, these are never
  reachable through the Caddy-exposed chat instance without their own explicit
  access decision.
- No replacement of the OpenCode tier system — this is a parallel consumer of
  `LOCAL_ENGINES`, not a new abstraction over it.

## Sources (primary, retrieved 2026-09-24)

- Open WebUI docs: main, connect-a-provider, env-configuration reference,
  MCP (`/features/extensibility/mcp`), Open Terminal, Computer, licence +
  LICENSE_HISTORY, alternatives pages (LibreChat/AnythingLLM/Jan), v0.11.4 release
- Open WebUI env-configuration reference re-verified 2026-09-24 during
  review remediation: `ENABLE_PERSISTENT_CONFIG` (default `True`; env only
  wins when `False`), `*_API_CONFIGS` (`prefix_id`, `connection_type`,
  `enable`, `model_ids`), `WEBUI_ADMIN_EMAIL`/`WEBUI_ADMIN_PASSWORD`
  bootstrap (auto-disables signup), no Anthropic-type env-var path
- LibreChat docs: ai_endpoints (incl. LiteLLM page), features, quick start
- AnythingLLM docs: LLM configuration (incl. oMLX page), model router, MCP,
  Agent Flows, scheduled jobs, telemetry note
- Jan README (features, OpenAI-compatible server, mlx-server)
- egoist/waku README + GitHub API metadata
- LiteLLM docs: proxy quick start, configs, virtual keys, endpoint matrix;
  licence (MIT core / enterprise dir)
- OpenRouter docs: pricing (5.5% platform fee), BYOK, privacy/provider-logging
- Anthropic Consumer Terms (effective 2025-10-08)
- Community signals: HN threads on the 2025 licence change (limited independent
  benchmarking evidence exists; Reddit sampling blocked during research — flagged
  as residual uncertainty on community sentiment, not on documented facts)

## Open questions for implementation (Phase 4, gated on approval)

Decisions already made in this design (not open): Ollama connects via its
native API only and is deduplicated out of the OpenAI-protocol array;
environment-authoritative config (`ENABLE_PERSISTENT_CONFIG=False`) is the
default policy; cloud providers are one-time named integrations; Caddy uses a
dedicated host by default; Meridian is excluded unless both conditions in
principle 3 pass.

1. Meridian authorization (user decision, not a research question): if the
   OAuth-backed Claude Code SDK path should also serve general chat,
   authorize it explicitly; otherwise it stays OpenCode/Mozart-only.
2. pip-in-venv for the LaunchAgent (repo has no Docker dependency; pip
   matches existing LaunchAgent patterns): verify `open-webui serve` behaves
   well under launchd (stdout, respawn) and choose the exact immutable pin.
3. Port allocation (`OPENWEBUI_PORT`, default 8080) and the concrete Caddy
   host name (e.g. `chat.<domain>`).
4. Validate the environment-authoritative mode in practice: confirm admin
   conveniences that should persist (model ordering, theme, workspace
   settings) survive restarts, and confirm the database-authoritative
   fallback is not needed.
