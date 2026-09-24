# Unified Multi-Provider Chat Frontend (Proposed)

> **Status: proposed, not implemented.** This document is the research-backed design
> for a unified chat frontend over the repo's local engines and cloud providers.
> Implementation (Phase 4) is pending approval; nothing here is wired up yet.

## TL;DR

**Recommendation: Open WebUI**, integrated the same way every optional service in
this repo is integrated — a gate, a generated LaunchAgent, a Caddy route, and
provider connections generated from the existing `LOCAL_ENGINES` registry.

- Open WebUI is the only candidate that ships the "whole package" you described:
  multi-provider chat **plus** Open Terminal **plus** Open WebUI Computer.
- The licence is non-OSI since v0.6.6 but fine for a personal deployment
  (≤50-user exemption; keep branding).
- The unified-frontend goal itself is **not an anti-pattern** — it is the
  well-established "personal AI gateway" pattern — as long as it is scoped to
  **API-accessible models**. Consumer subscriptions (ChatGPT Plus, Claude Pro)
  are *not* APIs and must stay in their vendor apps.
- The runner-up is **LibreChat** (MIT, lighter, strong agents/MCP) if you later
  want a cleaner chat-only surface without the platform scope.
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
| **Open WebUI** | **Recommended** | ~153k stars, very active (v0.11.4, 2026-09-21). Native Ollama + arbitrary OpenAI-compatible endpoints + named OpenAI/Anthropic integrations. MCP via Streamable HTTP (admin-configured). Open Terminal + Computer included. Non-OSI licence since v0.6.6 (fine ≤50 users; keep branding). Fast-moving: pin releases, never `:main`. |
| **LibreChat** | Runner-up | MIT, web-first, cleanest add/remove custom-endpoint semantics, first-class MCP + agents, LiteLLM optional. Lighter than Open WebUI; no Open Terminal/Computer equivalent. |
| **AnythingLLM** | Situational | Best if RAG/document-workspaces/Agent-Flows are the priority. Heavier than needed for plain chat; `DISABLE_TELEMETRY=true` required. Documents **oMLX** as a named local provider. |
| **Jan** | Complementary only | Desktop-first (Tauri); weak as a Caddy-exposed web frontend. Its OpenAI-compatible local server (`localhost:1337`, incl. an `mlx-server`) could serve as a local *provider*, not the UI. Apache 2.0. |
| **LobeHub** | Watch | Most active agent-operator platform (~82.8k stars, Apache 2.0); direction outgrew "chat switcher"; more product complexity than wanted. |
| **egoist/waku** | Excluded (wrong category) | Native coding-agent cockpit for Amp/Claude Code/Codex/Cursor/OpenCode/Pi via per-agent native protocols; young (created 2026-07-31, ~1.5k stars, GPL-3.0-only, active). No Ollama/provider catalogue, no generic OpenAI-compatible endpoint model. Track separately for the agent fleet. |

### What about Open WebUI's own `/alternatives/` pages?

First-party marketing, not neutral evaluations — unusually positive toward
competitors, consistently positioning Open WebUI as the broadest platform. The
competitor facts they state (LibreChat = focused multi-provider chat, MIT;
AnythingLLM = workspace-centred document Q&A; Jan = local-first desktop, Apache
2.0) check out against each project's own docs, so the pages are usable as
orientation but not as benchmarks. Their own feature counts drift between pages
(9 vs 13 vector databases) — treat claims as snapshots.

### Agentic sessions from a web UI — honest maturity read

- Open Terminal: real, stateful workspaces (files/shell/packages/processes) —
  runs in Docker or on host; treat as **privileged infrastructure**, not a
  hardened boundary. Multi-user isolated "Terminals" requires an enterprise licence.
- Open WebUI Computer (`cptr`): real but explicitly "cutting-edge" — signed-in
  users get keyboard-equivalent power over the machine; docs compare exposure to
  SSH and recommend private-network access. Treat as experimental.
- LibreChat workspaces: labelled "highly experimental" by the project itself.
- Industry-wide, a self-hosted web UI that reliably manages persistent arbitrary
  CLI agent sessions (resumability, multiplexing, permissions, audit) does not
  exist yet as an interoperable standard.

**Implication:** the "manage multiple agentic tooling sessions" itch is better
served by this repo's existing fleet (11 CLI agents + OpenCode web on :4096)
than by bolting agent orchestration onto a chat UI in 2026. Open WebUI Computer
is worth adopting as an *opt-in, localhost-only* experiment when we get to it —
it is genuinely useful (git worktrees, diffs, terminals per project) but must be
treated as a separate security boundary from the chat frontend.

## Recommended architecture

```text
                        ┌────────────────────────────┐
   browser (LAN) ──► Caddy ──► Open WebUI (LaunchAgent, :8080)
                        │        │  connections generated from
                        │        │  LOCAL_ENGINES + cloud providers
                        └────────┼────────────────────────────┘
                                 │
        ┌────────────┬───────────┴──────────┬──────────────┐
        ▼            ▼                      ▼              ▼
     Ollama        oMLX               OpenAI/Anthropic   OpenRouter
     :11434   :OPENWEBUI_…/v1          (named integrations/   (optional)
   (built-in)  OpenAI+Anthropic API    direct API keys)
```

### Design principles (mapped to repo patterns)

1. **Single source of truth: `scripts/lib/local_engines.py`.** Provider
   connections are *generated*, never hand-entered in the admin UI. The configure
   script emits `OPENAI_API_BASE_URLS` / `OPENAI_API_KEYS` (semicolon-separated,
   parallel order — verified against Open WebUI env-configuration reference,
   2026-09-24) from `active_engines()` + `local_endpoint_for()`, and
   `OLLAMA_BASE_URLS` for Ollama. Plug/unplug an engine = flip its
   `DOTFILES_RUN_*` gate + `make deploy`. The same registry already feeds
   OpenCode providers, Mozart, Caddy, and Junie — this adds one more consumer,
   not a new source of truth.
2. **Gate + LaunchAgent + Caddy, like every optional service.**
   A new `DOTFILES_RUN_*_SETUP` gate (default 0; concrete name chosen at
   implementation — see open questions), documented in `.env.example`
   (check-env-coverage), Layer-2 script `run_onchange_30-openwebui.sh.tmpl`
   (next free slot; hash-triggered on the configure script), LaunchAgent
   `com.openwebui.web` (mirrors `com.opencode.web`), Caddy route
   `/webui/*` behind the existing LAN allowlist + basic auth.
3. **Cloud providers via named integrations.** OpenAI/Anthropic/OpenRouter/Google
   connect with their own API keys from `~/.env` (upstream-native env vars; no new
   `DOTFILES_` names for provider keys). Meridian (Anthropic-compatible
   `:3456/v1`) is a **to-verify** integration during implementation — Open WebUI's
   Anthropic connection must accept a custom base URL for it to plug in; if it
   does not, Meridian stays an OpenCode/Mozart-only path (no hacks).
4. **No subscription proxying. Ever.** ChatGPT Plus / Claude Pro sessions are not
   API backends; they stay in their vendor apps.
5. **Security boundaries stay separate.** The chat frontend is Caddy-exposed with
   auth; Open Terminal and Computer start disabled and, when adopted, run
   localhost-only (Tailscale-style private network per upstream guidance), never
   exposed through Caddy in phase 1.
6. **No LiteLLM gateway initially.** oMLX already speaks OpenAI + Anthropic and
   Ollama is built in; both UIs connect directly. LiteLLM becomes a documented
   escalation if provider count, aliasing, fallbacks, budgets, or per-client keys
   grow — it is *not* part of the initial build (avoids over-layering; Mozart
   already exists for the OpenCode side).
7. **Pin releases.** Open WebUI moves fast; the LaunchAgent installs a pinned
   version (updated deliberately, not `:main`).

### What Open WebUI gives us beyond chat

- Model selector across all configured connections; multi-model chats
- Workspace models (wrap a base model with instructions/tools/knowledge)
- MCP tool servers (Streamable HTTP, admin-configured) — pairs with the repo's
  MCP registry; stdio/SSE servers need the upstream `mcpo` bridge (document as a
  limitation, not a workaround we build)
- RAG/knowledge bases (hybrid BM25 + vector), web search, scheduled automations
- Open Terminal (opt-in) and Open WebUI Computer (opt-in, localhost-only)

### Operations snapshot (from upstream docs, Sept 2026)

- Default SQLite + local ChromaDB is fine for one user; **not** for network
  filesystems or multi-worker. Postgres/Redis only if scaling later.
- Use the `main-slim` Docker image **or** pip install; personal footprint is
  plausibly 2–4 GB RAM without local RAG/embedding workloads (estimate, not an
  official minimum).
- Set a persistent `WEBUI_SECRET_KEY` (in `~/.env`; required for stable
  sessions/OAuth token decryption across restarts).
- Model lists auto-discover from endpoints; admin can pin/hide/order models.
  Preset refresh therefore needs **no repo artifact** — unlike OpenCode's
  checked-in catalogs, connection-provided models update themselves. Documented
  refresh flow: restart the service after changing engine gates; use the admin UI
  for pinning/ordering preferences (those are runtime state, not repo state).

## Maintenance guidance

### Adding a new local engine (e.g. a future local server)

1. Add an entry to `LOCAL_ENGINES` (`scripts/lib/local_engines.py`) with
   `gate_env`, `api` (must include an OpenAI-compatible protocol), base URL,
   `health_check`, and `caddy_route` if LAN-exposed.
2. The chat-frontend configure script picks it up automatically (it consumes
   `active_engines()` like every other consumer).
3. Document the gate in `.env.example`; run `make verify`.

### Adding a cloud provider

1. Named integration in Open WebUI (Settings → Admin → Connections) for
   OpenAI-compatible/Anthropic-style APIs, or
2. add the endpoint to the configure script's cloud-provider table
   (`scripts/lib/provider_endpoints.py`) with `baseUrl`/`apiKeyEnv`, so the
   connection is generated rather than hand-entered.
3. Document any new env var in `.env.example`.

### Model preset refresh

- **Local engines:** models are discovered live from each engine's `/v1/models`.
  Nothing to check in; add/remove a model in Ollama/oMLX and it appears. The
  existing `check-model-drift` gate continues to cover OpenCode catalogs.
- **OpenCode tier presets are unchanged** — this system is a parallel consumer of
  the same engines, not a replacement for the slim.json tier registry.
- Update pinned Open WebUI releases deliberately (bump the pinned version in the
  LaunchAgent template; check release notes for security fixes first).

### Documentation duties when touching this area

- Update this doc for architecture changes; keep `docs-drift` green (link from
  `AGENTS.md`/`README.md`).
- New gates → `.env.example` + `docs/ORCHESTRATION.md`.
- New Caddy route → `docs/CADDY.md` + `scripts/configure-caddy.py`.

## Explicit non-goals

- No subscription-proxying of ChatGPT Plus/Claude Pro (ToS + fragility).
- No LiteLLM/OpenRouter gateway layer in the initial build.
- No LAN exposure of Open Terminal/Computer in the initial build.
- No replacement of the OpenCode tier system — this is a parallel consumer of
  `LOCAL_ENGINES`, not a new abstraction over it.

## Sources (primary, retrieved 2026-09-24)

- Open WebUI docs: main, connect-a-provider, env-configuration reference,
  MCP (`/features/extensibility/mcp`), Open Terminal, Computer, licence +
  LICENSE_HISTORY, alternatives pages (LibreChat/AnythingLLM/Jan), v0.11.4 release
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

1. Meridian as an Anthropic connection: does Open WebUI's Anthropic integration
   accept a custom base URL? If not, Meridian stays out of the chat frontend
   (still available to OpenCode/Mozart).
2. pip-in-venv vs Docker for the LaunchAgent: repo currently has no Docker
   dependency; pip matches existing LaunchAgent patterns (opencode-web, meridian).
   Verify `open-webui serve` behaves well under launchd (stdout, respawn).
3. Port allocation (`OPENWEBUI_PORT`, default 8080) and Caddy path
   (`/webui/*`) vs optional `chat.<domain>` subdomain.
4. Whether to expose Ollama's native API additionally (`OLLAMA_BASE_URLS`) or
   route Ollama through its OpenAI-compatible endpoint only (fewer duplicate
   model entries — decide at implementation).
