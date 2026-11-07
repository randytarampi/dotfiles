# ChatGPT Desktop and Codex CLI: custom inference qualification

**Status (owner update 2026-11-07):** Codex CLI's LiteLLM route is **owner-reported working for several days**; its original finite acceptance issue [#82](https://github.com/randytarampi/dotfiles/issues/82) was closed. [LiteLLM's Codex-in-ChatGPT-Desktop guide](https://docs.litellm.ai/docs/proxy/client_setup/codex_chatgpt_desktop) now explicitly documents the shared `~/.codex/config.toml`. **The Codex desktop panel remains independently unverified** and is tracked by [#216](https://github.com/randytarampi/dotfiles/issues/216). Ordinary ChatGPT Chat and Work do not use this gateway configuration.

## Separate the products and consumers

- **ChatGPT Desktop Chat:** repo does not have a supported setting to replace the hosted ChatGPT model endpoint with the local LiteLLM gateway. Do not edit application internals or install a blanket TLS proxy.
- **ChatGPT Work:** a different product surface; Codex CLI `config.toml` does not establish an inference endpoint for Work.
- **Codex CLI:** supports `~/.codex/config.toml`, named profiles, provider base URLs and Responses wire API. The existing `configure-codex.py` provides a local `[model_providers.litellm]` only when the owner enables `DOTFILES_RUN_CODEX_SETUP=1` and `DOTFILES_USE_LITELLM_PROXY=1`.
- **Codex within the ChatGPT Desktop app:** LiteLLM's documented app path reads `~/.codex/config.toml` and can use a named LiteLLM provider/Responses alias; GUI launch environment, `~/.codex/.env` key access, model picker and actual task receipts are **UNKNOWN until owner verified**. Do not infer desktop proof from CLI success; see #216.

## Standalone Codex CLI opt-in qualification

1. Set **all three gates**: `DOTFILES_RUN_CODEX_SETUP=1`,
    `DOTFILES_USE_LITELLM_PROXY=1`, and `DOTFILES_RUN_LITELLM_SETUP=1`.
    The third gate is required for the managed shell export of the private
    `codex.key`. Alternatively, provide `LITELLM_CODEX_KEY` explicitly in the
    launched Codex process after verifying the key's identity and scope.
    Provide a dedicated, valid `LITELLM_CODEX_KEY` through the existing
    private, owner-controlled
    provisioning procedure. Do not create or rotate the key in this PR.
2. Set `DOTFILES_CODEX_LITELLM_MODEL` to an **exact served** gateway alias
    that accepts `/v1/responses`, e.g. a verified alias of the form
    `openrouter/vendor/model:free`. This example is syntax, not a claim
    that a particular route is present, free or Responses-compatible.
3. Generate config with `scripts/configure-codex.py --dry-run`, inspect
    `~/.codex/config.toml` for an unmanaged-collision refusal, and only
    then approve a normal run. The profile is omitted when the model is
    unspecified; the provider and user-owned root default are separate.
4. Test in an explicitly bounded terminal session:

    ```sh
    codex --profile litellm
    ```

    Inspect `codex --help` for the installed version's exact syntax.
    Record selected model, actual `/v1/responses` request, streaming,
    tool calls, cost/usage and gateway receipt. Preserve the direct
    OpenAI default and test switch-back.
5. Test Codex inside **ChatGPT Desktop** independently ([#216](https://github.com/randytarampi/dotfiles/issues/216)). Inspect effective
    app version, selected model/provider and requests. If it does not
    honour the CLI profile, record UNSUPPORTED or UNKNOWN, not a fabricated
    desktop launcher or inference override.

## P0 ChatGPT Desktop Codex-panel acceptance ledger (#216)

**Current claim:** Codex CLI is owner-reported working with LiteLLM. That does not establish the **Codex panel inside ChatGPT Desktop** uses a custom profile; ordinary ChatGPT Chat and Work remain separate hosted consumers. [LiteLLM's desktop Codex guide](https://docs.litellm.ai/docs/proxy/client_setup/codex_chatgpt_desktop) documents the shared ~/.codex/config.toml and warns that GUI apps may not inherit shell-exported virtual keys. It illustrates the private ~/.codex/.env path; this repo has not qualified a managed owner-preserving delivery for that file.

### Read-only checks before any owner-approved GUI change

1. Record exact ChatGPT Desktop and Codex versions. Use the app's **Settings → Configuration → Open config.toml** and inspect provider/profile identifiers without copying secrets. Do not change the root model or provider merely because a named profile exists.
2. Inspect the current managed model_providers.litellm block, wire_api=responses, exact served alias and profiles.litellm. The CLI and GUI may disagree on what a named profile means; compare selected effective settings rather than generated text alone.
3. Inspect **only presence, ownership and restrictive mode** of the dedicated key in the runtime GUI environment; do not print the value, write a new ~/.codex/.env automatically or assume a shell export reaches the Desktop app.
4. With explicit owner consent, select the gateway model inside the Codex panel and run a harmless read-only task that actually issues a Responses request. Capture a redacted LiteLLM virtual-key request receipt, streaming result and tool handling. If the GUI can't select the profile, record UNKNOWN/UNSUPPORTED; do not force the root default as a workaround.
5. Exercise unavailable gateway, denied credential and unsupported model; verify no undocumented direct or paid fallback. Restore hosted mode without modifying Chat, Work, account/subscription settings, local history or Codex CLI defaults.

| Consumer | Evidence needed | Status as of 2026-11-07 |
|---|---|---|
| Codex CLI | Owner's working LiteLLM experience; not repeated | OWNER-REPORTED |
| Codex in ChatGPT Desktop | GUI-selected model, Responses/tool request and scoped gateway receipt | UNKNOWN |
| Ordinary ChatGPT Chat | No supported custom inference endpoint through Codex config | NOT ROUTABLE BY THIS PROFILE |
| ChatGPT Work | No supported custom inference endpoint through Codex config | NOT ROUTABLE BY THIS PROFILE |

**Handoff:** keep existing model registry and dedicated credential; no new Desktop-only provider catalogue, TLS interception, public gateway listener or app internals edits. Current claim remains restricted to source/profile readiness until the owner submits a versioned GUI receipt. Separate the successful desktop task from account and privacy boundary testing.

## Negative matrix

- Missing/unsafe/rotated key; missing gateway, invalid exact model alias; `/v1/responses` unsupported; malformed/unmanaged `config.toml` collision; gate-off mode; hosted/default mode unaffected; no hidden billed fallback; no shell/GitHub credentials accidentally written into Codex config.
- Existing model-specific `model_reasoning_effort`, marketplace, plugin, desktop settings, MCP and other user-owned sections must survive. Run generation twice and compare bytes/permissions.
- For each surface, record VERIFIED / OWNER-REPORTED / NOT RUN / UNKNOWN / UNSUPPORTED and a concrete provider/model/gateway receipt. No ChatGPT app internal files or private settings should be modified.

## Reviewer handoff

Run `pytest -q scripts/lib/tests/test_configure_codex.py`, `make check-env-coverage`, `make verify`, then inspect exact-head GitHub CI. The owner reports the installed Codex CLI gateway route working (2026-11-07, not independently replayed); recheck the CLI only after a relevant change or regression. A static TOML profile still does not qualify the desktop GUI; #216 is the independent acceptance task.

**Upgrade:** without an explicit model, this change retires an older managed `[profiles.litellm]` and emits a warning. If you depend on the existing profile, set `DOTFILES_CODEX_LITELLM_MODEL` to its still-served alias before regeneration. Review the private config backup before rollback.

**Rollback:** unset `DOTFILES_CODEX_LITELLM_MODEL` and regenerate the marked profile with an approved backup. Never delete private Codex configuration, tokens, history or existing sessions automatically.

**Attribution:** Original ChatGPT (GPT-6) drafting, 2026-11-03. No actual ChatGPT Desktop operation, Codex CLI inference or owner credentials were exercised **by that drafting session**. The subsequent owner-reported Codex CLI success on 2026-11-07 is recorded above; Desktop remains unverified.
