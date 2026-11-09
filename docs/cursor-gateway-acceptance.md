# Cursor Desktop and CLI — bounded LiteLLM acceptance

> P1/P2 [#219](https://github.com/randytarampi/dotfiles/issues/219), baseline `main@ab5077e30` (2026-11-07). This is an executable investigation plan, **not** a working Cursor inference implementation.

## State and interface boundary

- `scripts/lib/litellm_config.py` provisions a scoped Cursor virtual key only when the existing app/install/opt-in gates qualify; [unmanaged-clients.md](unmanaged-clients.md) documents operator-run GUI setup but **does not write Cursor settings**.
- [LiteLLM Cursor guide](https://docs.litellm.ai/docs/tutorials/cursor_integration#setup) uses **`<LITELLM_BASE_URL>/cursor`**, an exact public model alias and scoped virtual key. It documents Ask/Plan/Agent; Agent protocol translation depends on LiteLLM v1.97+. Do not change this to plain `/v1` or silently assume all Cursor model modes support API-key overrides.
- The **Cursor Desktop** UI and **Cursor CLI** `agent`/`cursor-agent` may use different credential, routing, mode, session and network origins. `CURSOR_API_KEY` is not `LITELLM_CURSOR_KEY`. Cursor Tab/Auto/Background/Cloud Agents are outside the initial desktop BYOK scope.
- A provider key and `/cursor` URL may be *correct but unreachable*: if Cursor's servers originate inference requests, the host's `http://127.0.0.1:4000` refers to Cursor's server, **not** the developer's Mac. A Tailscale-only address is likewise not automatically reachable from a vendor service.

## Workstream A — independently qualify request origin

Record exact installed desktop build, CLI build, selected model/mode and documented custom inference policy. Prove traffic direction with safe local loopback observation or vendor documentation; distinguish locally originated Ask/Plan/Agent versus server-originated requests. If a server-originated request requires external HTTPS, **stop before exposing LiteLLM**. Obtain an independent owner-approved authenticated ingress design and threat review. Unsupported/restricted modes must be labelled as such, not configured by guessing.

## Workstream B — scoped desktop integration, only after A

Keep current owner-selected settings/credentials intact. With the installed supported mode, manually set `Override OpenAI Base URL` to `<approved-gateway>/cursor`, use the existing private `~/.local/share/litellm/clients/cursor.key`, choose one exact currently served model alias, and confirm a single Ask task and attributable gateway receipt. Then test Plan and Agent separately if enabled; verify correct tool calls, 401/429, alias mismatch, 5xx, gateway outage, restart and return to native provider. Preserve Cursor Tab/Auto and project/workspace settings.

If the only supported path needs public ingress, document that result and defer until separately authorised; no automatic tunnel, public admin endpoints, master key or broad Caddy access.

## Workstream C — distinct Cursor CLI integration

Inspect the actual `agent` / `cursor-agent` flags, provider/profile path and whether a direct custom LLM is supported at all. Do not assume Desktop GUI preferences control CLI model inference. If native gateway routing is available, create a small opt-in wrapper reusing the same authenticated model alias but a **distinct credential** if service identity differs. Do not replace `CURSOR_API_KEY` in the Cursor CLI session without verifying the CLI contract. Test one CLI code/read task and rejection paths in a disposable worktree.

## Verification and rollback

Source contract: `scripts/lib/litellm_config.py`, `scripts/lib/litellm_client_auth.py`, `docs/unmanaged-clients.md`, `docs/capability-matrix.md`; `PYTHONPATH=scripts/lib pytest -q scripts/lib/tests/test_litellm_client_registry.py scripts/lib/tests/test_litellm.py`, `make check-cli-contract`, `make verify`, with new fixture coverage for exact alias/virtual key, gate-off, symlinks, no unsafe persistent writes, and false `/cursor` host assumptions. Owner handles any GUI edits, external endpoint, direct charges and required host receipts.

Rollback: restore original Cursor native model/provider and any private backup; never delete UI settings/history or deprovision unrelated key. No changes to user state in this documentation PR. Record per-surface `SUPPORTED`, `UNSUPPORTED`, `UNKNOWN`, `OWNER-REPORTED` and `CONSUMER-VERIFIED` outcomes; do not close #219 until installed-client route and real failure proof or an explicitly accepted unsupported classification exist.

## 2026-11-09 disposition (issue #219 closure)

**UNSUPPORTED (versioned)**: the gateway registers no Cursor wire (LiteLLM 1.106.6 running config) and `cursor-agent 2026.08.11-e8db854` performs client-side third-party key-format validation — a scoped LiteLLM virtual key is refused before any request ("The provided API key is invalid") on both tested endpoint forms, while the same key validates HTTP 200 at `/v1/models`. Desktop Ask/Plan/Agent are server-origin and cannot reach the loopback gateway; no ingress was approved (owner decision 2026-11-09). The scoped `cursor.key` remains provisioned for future use. Re-entrant: a bearer-key-capable cursor-agent release or a separately approved ingress design reopens this with the recorded denial receipts as the baseline.
