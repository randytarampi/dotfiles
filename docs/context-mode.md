# Context Mode — guarded OpenCode pilot

**Assessment (17 November 2026):** upstream `context-mode@2.2.1`, source-verified permission checks after [#1331](https://github.com/mksglu/context-mode/issues/1331) closed; installed OpenCode, Node, agent hooks and permission enforcement on the owner's hosts **not run**. This is an opt-in pilot, not production acceptance.

## Scope and ownership

Context Mode keeps large tool results outside the prompt and indexes them in a per-project local SQLite FTS5 database under `~/.context-mode`. It supplies `ctx_execute`, `ctx_batch`, `ctx_index`, `ctx_search`, `ctx_resume`, `ctx_stats` and `ctx_doctor`. Its tool-output 'sandbox' is not an operating-system sandbox: executed code still runs with the user's file, environment and network privileges.

**Do not replace or double-register existing components.** DCP remains the sole OpenCode compaction owner at 33%/67%; native `compaction.auto` remains false. Context Mode's native pre-compaction callback may not see DCP's own compression. OMO-Slim retains agent orchestration and model fallback. Basic Memory remains the shared durable-note system, CodeGraph source intelligence, Context7 external documentation, Plannotator planning, Tokenscope cost reporting and voice its native TUI route. VibeGuard still protects model-bound material, but it is not proof that Context Mode will not store raw secrets locally. The opt-in generator explicitly asks for `ctx_execute`, `ctx_batch`, `ctx_index` and `ctx_purge`, and denies agent-initiated `ctx_upgrade`. These are ordered last to defeat silent generic allows; native `shell` denial alone does not restrict JavaScript/Python execution through `ctx_execute`. The hosted Context Mode Gateway is excluded; do not add any new inference proxy or public MCP service.

## Opt-in installation

Set `DOTFILES_RUN_CONTEXT_MODE_SETUP='1'` in private `~/.env` and use the existing `DOTFILES_RUN_OPENCODE_TOOLS_SETUP=1` and `DOTFILES_RUN_OPENCODE_SETUP=1` setup gates. The new gate defaults off. Script 07 checks **Node >= 24.15.0 before plugin changes**; the canonical manifest pins `context-mode@2.2.1` rather than floating latest. The global OpenCode generator includes this plugin only when opted in. It never writes a duplicate Context Mode MCP registration to `configs/mcp/global-mcps.json`; the OpenCode plugin registers its tools and hooks.

Gate-off generation emits `-context-mode` to suppress a previously installed package in OpenCode's plugin cache, just as `-opencode-mem` suppresses the retired memory plugin. The suppression also disables a user-installed Context Mode package under this managed global configuration; it never deletes the package cache, indexed files or user-owned data. Project-scoped overrides must be independently checked. Do not run upstream's fleet-wide `context-mode setup` over existing dotfiles-owned agent configurations: it can mutate client hooks and MCP settings independently of the repo's reconcilers.

## Adversarial acceptance requirements

1. In disposable HOME/project settings, gate **off**: no installation, exactly one negative directive, no duplicate MCP or plugin, no changes to DCP/Basic Memory/VibeGuard, and two regenerations produce the same bytes.
2. Gate **on**: install 2.2.1, verify exact resolved version, run `ctx_doctor` in OpenCode v2, inspect actual hooks and `ctx_*` availability, index and retrieve harmless synthetic source output.
3. Permissions: deny, ask and allow each must work for native shell **and** `ctx_execute(language="shell")` / `ctx_batch`; separately verify `ctx_execute(language="javascript")` and Python respect the explicit `ctx_execute` ask rule even when native shell is denied; deny also works for file reads, external directories and private URLs. Confirm no denied command executes, even when permissions are 'ask' by default. The upstream #1331 source fix is not a host receipt.
4. Interaction: synthetic secret-shaped test outputs must not reach model inference; inspect whether Context Mode stores them at rest despite VibeGuard and require restrictive local permissions. Check Tool/Read/WebFetch rewrites, failures, approval prompts, source fidelity, full PR and CI logs, and OMO-Slim subagent tool dispatch.
5. Compare DCP-only, DCP+Context Mode and (in a separate throwaway configuration) native compaction+Context Mode. Record compaction hook events and after-compaction recovery, not just one prompt. Preserve Basic Memory note reads/writes and CodeGraph results.
6. Measure *correct tasks completed*, wall time, prompt/cache tokens, spend and error retention over verbose `make verify`, GitHub reviews and local model sessions. Upstream's large-output byte-saving median is not total-token or correctness evidence.
7. Test missing Node, bad plugin package, interrupted install, offline start, disabled gate and restart. Never purge `~/.context-mode`, `~/basic-memory`, `~/.basic-memory` or `~/.opencode-mem/data` in rollback.

Source checks: `make check-plugin-consistency`, `make check-hashes`, focused pytest and `make verify` on the exact PR head; installed-client acceptance requires the owner's compatible development host. Keep the gate off until missing evidence is supplied. Additional Pi, Claude, Codex, Gemini, Copilot CLI and IDE integrations require separate native hook/permission qualification; do not register one unguarded Context Mode MCP globally across the 15-client fleet.

## Rollback

Set `DOTFILES_RUN_CONTEXT_MODE_SETUP='0'`, regenerate global OpenCode config and restart the application. Confirm `-context-mode` is present and DCP still owns compression. If the installed host continues loading Context Mode despite the negative directive, stop and use an owner-reviewed `opencode plugin remove context-mode` operation. Preserve indexed and memory data. Do not automatically run `ctx_purge`.

**References:** [Upstream](https://github.com/mksglu/context-mode), [permission fix #1331](https://github.com/mksglu/context-mode/issues/1331), [DCP](dcp.md), [Basic Memory](basic-memory.md). **Contributors:** ChatGPT (GPT-6): research, implementation and source review; owner: deployment and acceptance (not performed).
