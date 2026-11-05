# Paseo fleet follow-up implementation plan — 5 November 2026

**Status:** executable, documentation-only proposal. **Baseline:** main at 1fbec012dce4a206dde54b5185651ea93ec89da3 plus draft stack #175 → #176 → #177. Exact heads and CI must be refreshed before implementation. This document is the authoritative raw Markdown plan.

**Mission:** extend existing tooling rather than add a second registry. Govern all changes through AGENTS.md, configs/agents/{home-agents,repo-agents-shared}.md, configs/review/code-review-prompt.md, .github/skills/code-review/SKILL.md and the PR template. Preserve users' configs, credentials, plugin data, existing MCP entries, published model aliases, direct-versus-LiteLLM policy and owner-only live operations.

**Blocking inherited limitation:** issue #173 establishes that Paseo CLI/UI/daemon config writers lack cross-process transaction protection. No unattended Paseo native config writes. An isolated preflight, post-write check, or wrapper-only flock is not a shared CAS. Do not close #173 without a deterministic two-writer test proving preservation or pre-write conflict refusal.

## Existing bounded stack

| Draft | Base | Delivers | Still requires |
|---|---|---|---|
| [#175](https://github.com/randytarampi/dotfiles/pull/175) | main | Switch unattended Paseo Web native edits to read-only; explicit owner-only apply | CI, quiescent owner readback, LAN/public WebSocket/TLS/auth |
| [#176](https://github.com/randytarampi/dotfiles/pull/176) | #175 | oMLX pinned/favourite status carried to speech discovery and Open WebUI | exact qualification tests, two-run readback, real voice |
| [#177](https://github.com/randytarampi/dotfiles/pull/177) | #176 | desired plugin installer with explicit apply; CodexBar PATH; machine-status compatibility refusal | latest-head checks; inspect plugin source and actual daemon |

Do not combine further functionality into these PRs just to reduce PR count. Once each parent merges, retarget and recheck each dependent diff. Child implementation branches should start from the smallest merged prerequisite, not automatically include every unrelated proposal.

## Proposed PR D — detected ACP providers (new implementation PR; not opened)

**Entry points:** scripts/configure-acp-agents.py (OpenCode `acpAgents` inventory input, **not** a Paseo provider schema), scripts/configure-paseo-acp.py (explicit Paseo schema translation and owner-only native writer), scripts/install-acp-adapters.py, scripts/configure-paseo-hermes.py, docs/HERMES_PASEO.md, docs/CAPABILITY_MATRIX.md, and tests.

**Contract:** read native-tool install intent from Brewfile.dev, Brewfile.desktop.dev, winget manifests and existing ACP registry. Native Paseo providers Claude/Codex/OpenCode/Pi need no duplicate overrides. ACP candidates include Cortex, Cursor, Gemini, Copilot, Hermes, Junie and existing bridges. Check the installed binary and exact documented ACP arguments before producing a custom provider entry; resolve binary to absolute path, do not shell-launch. Preserve Hermes existing entry. Classify available, missing, unsupported or unknown. Apply only after owner approval and quiescence under #173; never from make deploy.

**Acceptance:** isolated-HOME collision/no-write tests; correct provider IDs and schema; installed client initialise, streamed task, tools/approval and denial, cancellation, reconnect, workspace and model selection. Missing version/ACP, user collision, malformed JSON, shell-injection attempts and CLI normalisation fail closed. A documented unsupported result is an acceptable conclusion. Do not conflate ACP connectivity with LiteLLM inference qualification (#82). Rollback only exact managed provider entries.

## Proposed PR E — Paseo MCP tools and Xcode MCP (new implementation PR; not opened)

**Entry points:** configs/mcp/global-mcps.json, configs/mcp/templates/, configs/mcp/, scripts/configure-mcps.py, scripts/configure-mcp-tool.py, scripts/check-mcp-parity.py and tests.

**Paseo:** enable native daemon.mcp.enabled and daemon.mcp.injectIntoAgents only through explicit, #173-safe owner operation. Preserve existing native MCP service ownership and limit agent creation/termination to authorised orchestration contexts; do not assume all worker agents may control unrelated workspaces. Test both an allowed read-only action and denied termination/access.

**Xcode:** Apple documents xcrun mcpbridge, available once Xcode Settings > Intelligence > Allow external agents to use Xcode tools is enabled. Add paired canonical MCP template and per-consumer opt-in registration only on qualified macOS/Xcode hosts, not Windows/Linux. Xcode MCP capabilities and the existing IntelliJ MCP server are distinct. Test exact command/stdio handshake, one read-only project query and denied write with Xcode open; protect existing MCP registrations and refuse unknown xcrun/disabled Xcode permissions. Do not expose bridge via Caddy. No implicit Xcode permissions toggle.

## Proposed PR F — local speech and dedicated LiteLLM Paseo credentials (implemented in follow-up PRs #182–#183, subject to review)

**Entry points:** scripts/lib/{omlx,local_engines,selected_service,route_probes,litellm_config}.py, scripts/configure-opencode-voice.py, scripts/configure-litellm.py, scripts/lib/openwebui.py, docs/VOICE.md and tests.

**Qualification-first choice:** only inference-visible typed audio_stt/audio_tts models, with exact authenticated route/protocol/response evidence. Among equally qualified candidates prefer pinned > favourite > existing discovery order. Retain explicit user preference and a still-qualified prior selection. The screenshot shows mlx-community/Qwen3-ASR-1.7B-8bit and mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit pinned, favourite, API-enabled and loaded; this is not a live protocol proof. Do not hard-code these names, auto-download unqualified models or include known-unqualified TTS.

**Transport:** when DOTFILES_USE_LITELLM_PROXY=1, use only exact served LiteLLM aliases and a dedicated, registry-owned LITELLM_PASEO_KEY stored in clients/paseo.key by extending `scripts/lib/litellm_config.py` `APP_KEYS`/`CLIENT_REGISTRY`, `scripts/configure-litellm.py` provisioning/writer, private key-file parity, environment coverage and strict gate-off readback tests; existing provisioning alone cannot create a new alias. Check protocol and key delivery before promoting. Otherwise use directly qualified oMLX or Ollama loopback routes. STT and TTS qualify separately. No gateway failure may fall back direct and no key may be copied from OpenCode/Open WebUI.

**Consumer:** Paseo supports OPENAI_STT_API_KEY, OPENAI_STT_BASE_URL, OPENAI_TTS_API_KEY, OPENAI_TTS_BASE_URL, but persisted Paseo per-feature settings may take precedence. Use a shared resolver with client-specific adapters, not universal exports of generic OPENAI_API_KEY. Validate Paseo dictation, voice STT, TTS, restart/env and credentials; test 401/404/429/500, wrong voice ID, duplicate IDs, expired evidence, read-only versus probe, invalid models and no paid fallback. Reuse for other repo voice clients only with proven compatibility. No native Paseo writes on deploy.

## Proposed PR G — Pi-backed local metadata (new implementation PR; not opened)

**Entry points:** scripts/configure-opencode-tier.py, scripts/lib/{tier_registry,tier_resolve,local_engines}.py, scripts/configure-pi.py, Paseo metadata config/docs and isolated fixture tests.

**Settled preference:** Paseo agents.metadataGeneration.providers should start with { "provider": "pi", "model": "<actual Pi-visible local orchestrator model>" }. Resolve the actual local orchestrator selected from the effective OpenCode tier, materialise placeholders through the existing tier resolver and map the provider/model to Pi's configured, served local model ID. Never guess from a raw _local: placeholder and never route a cloud orchestrator through Pi as if local.

**Important limitation:** Paseo documents fallback from the configured metadata providers to built-in model candidates and finally the current agent model. A Pi-first array is NOT a strict local-only guarantee. Establish a supported way to suppress cloud fallback or explicitly report the limitation and do not enable strict-local claims. Use a paid-route/egress spy in failure tests. Acceptance: one real title/branch/commit/PR metadata generation path on local Pi with actual local receipts, no unexpected cloud requests; failed local route refuses or visibly degrades without silent paid cross-routing. Store native settings only through owner quiescence; preserve user-defined metadata preferences.

## Proposed PR H — password, Caddy and #173 closeout (new implementation PR; not opened)

**Entry points:** scripts/lib/paseo_web_policy.py, scripts/configure-paseo-web.py, scripts/configure-paseo-tailnet.py, scripts/configure-caddy.py, docs/PASEO_REMOTE.md, docs/CADDY.md and actual daemon supervisor settings.

**Password:** upstream Paseo already supports PASEO_PASSWORD in the daemon's environment and hashes it at startup. paseo daemon set-password requires an interactive terminal; do not pipe secrets or invent a noninteractive flag. A secret in ~/.env only works if the actual Paseo daemon (CLI/launchd/Desktop) inherits it securely. Investigate the real process owner and permission-protected environment first. Avoid shell history, printed passwords, plist disclosures and repeated destructive hash rewrites. If native Desktop owns the daemon, use supported owner setup rather than assuming dotfiles controls its launch.

**Caddy:** target the shared `CADDY_ACCESS=localhost|lan|public` exposure contract introduced by [PR #187](https://github.com/randytarampi/dotfiles/pull/187) (pending as of this revision); `DOTFILES_PASEO_PUBLIC` is retired by that PR, not an additional opt-in. Rebase and reconfirm before merge. Caddy-backed Paseo stays loopback with relay disabled, hostname allowlist and native password, while direct Tailscale-IP binding is an alternative topology. Verify actual listener, TLS, DNS, websocket upgrade, right/wrong auth, hostname denial and no accidental public/tailnet exposure. Restart requirements must be distinguished from persisted settings success.

**#173 closure condition:** native CLI, daemon settings UI and wrappers share a real cross-process lock or CAS, verified by concurrent disjoint-key writers and interruption failures; otherwise keep owner-only quiescent operation and issue open. Never roll back a newer unrelated writer's state.

## Cross-cutting verification, ownership and deferred work

Every implementation PR needs: exact inspected base/head, touched files and consumers, negative and positive acceptance, data ownership/classification, rollback, current-head CI/CodeQL/Qlty, PYTHONPATH=scripts/lib poetry run python -m pytest on relevant suites, make check-cli-contract, make check-env-coverage, make check-hashes, make check-mcp-parity where applicable, and make verify. Register new public scripts in scripts/lib/cli-contract.json. Isolate HOME and test symlink/duplicate/ownership refusals, repeat deployment and unchanged user state. Record verified / owner-reported / not run / unknown explicitly; mocked JSON or green CI do not prove a live client works.

**Owner-only checks:** source-review plugin permissions, live Paseo daemon and actual binary/CLI versions, approval denial, real voice routes, private LiteLLM credentials, Xcode permission, Caddy public/Tailscale access, and configuration quiescence. Stop before unapproved destructive tests, auth updates, Docker process actions, private file changes, public listeners or spent cloud requests.

**Deliberately deferred:** direct Telegram/WhatsApp command broker, public LiteLLM/oMLX/Xcode endpoints, Basic Memory migrations, changes to #85 embeddings/index data, #87 paid/free runtime admission, unrelated tier changes, automated cross-process rollback, and unverified machine-status on Paseo 0.15.

References: https://paseo.sh/docs/supported-providers#acp-catalog ; https://paseo.sh/docs/custom-providers ; https://paseo.sh/docs/mcp ; https://paseo.sh/docs/voice ; https://paseo.sh/docs/metadata-generation ; https://paseo.sh/docs/configuration ; https://developer.apple.com/documentation/xcode/giving-external-agents-access-to-xcode ; https://github.com/jundot/omlx/blob/main/omlx/server.py ; https://github.com/randytarampi/dotfiles/issues/173 .


## Implementation and integration checkpoint — 5 November 2026

This was an initial plan; follow-up source PRs [#179](https://github.com/randytarampi/dotfiles/pull/179) through [#185](https://github.com/randytarampi/dotfiles/pull/185) now cover ACP adapter, Paseo MCP, Xcode MCP, dedicated LiteLLM credential, speech, metadata and read-only web/auth audit. They remain subject to current-head CI, Copilot/Qlty review and owner-only verification. **Do not treat the plan's original "proposed" labels as evidence that those paths are not implemented.** In particular, #187 changes the Caddy exposure contract; if it merges first, rebase this stack and remove any audit logic requiring the retired public opt-in.
