# Context Mode — opt-in native context management

**Source contract (18 November 2026):** Context Mode is installed on its upstream **latest** release channels, behind the default-off `DOTFILES_RUN_CONTEXT_MODE_SETUP=0` gate. Source integration is distinct from real installed-host approval. [Upstream](https://github.com/mksglu/context-mode) currently supports 11 native agent integrations; other applications cannot gain its native tool-output hooks merely by registering a generic MCP server.

## Architecture

Context Mode intercepts and indexes large tool results before they consume an agent's prompt budget, makes them searchable, and restores session state after native compaction. Each agent retains its host-native tool, hook and skill registration. Context Mode's “sandbox” limits *context output*, **not** executable code's access to user files, environment or network. Local indexed outputs under `~/.context-mode` may contain confidential data.

It **augments, rather than replaces**, existing components:

- **DCP:** OpenCode compaction owner at 33%/67%; native `compaction.auto=false` remains. Verify whether Context Mode observes DCP compression before transferring ownership to native compaction.
- **Basic Memory:** durable user-owned Markdown notes and hybrid search through the shared 15-client MCP registry. Context Mode's per-project tool-output cache is not cross-agent durable memory; do not write it into Basic Memory automatically.
- **CodeGraph / Context7:** code intelligence and external library references. Prefer indexing only oversized *results*, not re-indexing their whole stores or replacing their server registrations.
- **OMO-Slim / Pi subagents:** native agent orchestration, roles, model pools and task selection stay authoritative. Context Mode hooks can filter oversized output returned from those consumers; a subagent lacking `ctx_search` may need intact output. Test actual role access.
- **VibeGuard:** secret filtering for its existing tool/model path, not guaranteed at-rest redaction for Context Mode's SQLite stores. Denied commands, errors and approval prompts must never be transformed into apparent success.
- **Plannotator, Tokenscope, voice, LiteLLM, Meridian, oMLX, Open WebUI:** maintain their own presentation, telemetry, audio, inference and server responsibilities. Context Mode does not transparently compress model *inputs* on those inference services. The hosted Context Mode Gateway is a separate paid inference boundary and is not added.

## Native installation and opt-in

Keep `DOTFILES_RUN_CONTEXT_MODE_SETUP='0'` unless enabling Context Mode. Set it to `1` in private `~/.env`, then run `make deploy` with the applicable existing tool setup gates enabled. The OpenCode manifest uses `context-mode@latest` and its native plugin installer, with Node >=24.15.0 required; the OpenCode generator includes the plugin only while opted in. Its permission rules require explicit approval for `ctx_execute`, `ctx_batch`, `ctx_index` and `ctx_purge`, and deny agent-triggered `ctx_upgrade`. These rules apply to JavaScript/Python execution too; native shell permissions alone are insufficient. They gate *model-invoked* calls only: Context Mode indexes oversized tool results from its own post-tool hooks, which invoke no tool and therefore no approval, so redacted model output can still be written to the local index unredacted.

The approvals rely on the host exposing `ctx.permission.assert`. OpenCode v2.3.2 does; on an older build `ctx_doctor` reports check `D10` as failed and the `ctx_*` rules cannot apply, so upgrade OpenCode rather than treating a green deployment as enforcement.

When disabled, generated OpenCode configuration includes `-context-mode` to suppress the previous package cache without deleting user data. **Do not simultaneously add** a `context-mode` MCP entry in `global-mcps.json`: an OpenCode plugin already registers its own tools and hooks, and duplicate entries can race on session events.

Use the upstream native mechanisms for other supported agents: Pi/OMP package, Claude Code and Codex marketplaces, Gemini extension, and the upstream `context-mode setup --yes --agents` workflow where hooks, permissions or MCP configuration require explicit merging. Native agent clients supported by the current upstream release are Claude Code, Codex, Gemini CLI, Qwen Code, Copilot CLI, VS Code Copilot, Kiro, ZCode, Pi, OpenCode and OMP. Claude Desktop, Codex Desktop, Cursor, Junie, JetBrains/IntelliJ, Antigravity, Cline, Cortex, Open WebUI and oMLX are *not automatically native Context Mode consumers*. A generic MCP registration alone would expose some functions but not provide the tool interception and compaction hooks that produce the advertised savings.

## Verification and reconciliation

For each configured native agent, test a real session with large file reads, verbose `make verify`, browser/WebFetch, MCP/CodeGraph output, denied shell and JavaScript/Python execution, tool errors, plugin reload, subagent fan-out and compaction. Compare correctness, tokens and cost with and without Context Mode, including absence of secret leakage to model prompts and stored SQLite. Repeat deployment to establish idempotency; inspect actual resolved plugin versions and load order rather than interpreting successful config generation as live proof.

Source evidence: `make check-plugin-consistency`, `make check-hashes`, focused regression tests, and exact-head `make verify` plus required CI. Host-only evidence requires the owner. Revert the integration PR(s), `git pull` and `make deploy` for repository-level rollback; package caches and previously installed native extensions are not automatically deleted by a source revert.

**References:** [DCP](dcp.md), [Basic Memory](basic-memory.md), [OpenCode](opencode.md), [Modernization roadmap](modernization-roadmap.md). **Contributors:** ChatGPT (GPT-6), source implementation/review; owner, desired integration policy and rollout decisions.
