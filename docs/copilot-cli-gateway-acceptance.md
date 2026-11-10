# Copilot CLI native BYOK and local Ollama acceptance

> P1/P2 [#218](https://github.com/randytarampi/dotfiles/issues/218) · `main@ab5077e30`, 2026-11-07 · source-level plan and implementation handoff, **not** live Copilot CLI acceptance.

## Baseline, actual consumers and disagreement to resolve

The repo already ships [`scripts/copilot-litellm`](../scripts/copilot-litellm), a deliberately isolated launcher that sets `COPILOT_PROVIDER_BASE_URL`, `COPILOT_PROVIDER_API_KEY`, `COPILOT_MODEL` and compatibility flags, while **refusing to run if `COPILOT_PROVIDERS_CONFIG`, `COPILOT_HOME` or `~/.copilot/providers.json` exist**. It also provisions a scoped `copilot-cli.key` on eligible hosts. This is not proof Copilot CLI's *native* provider registry selects that route.

GitHub's [current Copilot CLI custom provider guide](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-llm-models) documents `~/.copilot/providers.json`, `COPILOT_PROVIDERS_CONFIG`, custom OpenAI-compatible/Anthropic providers and native local Ollama discovery. **Credentials in `providers.json` are literal plaintext**; strings resembling `$VARIABLE` are not expanded. Do not generate a tracked file or rely on environment substitution that the installed CLI does not perform.

## Required acceptance path

**PR A — inspect and qualify exact native contract:** Record installed Copilot CLI version and schema, real config precedence, supported key helper/secret storage if any, and account authentication. Inspect the actual `providers.json` shape; determine whether OpenAI-compatible model IDs and tool calls can be selected. Do not infer from the legacy launcher. A read-only preflight must refuse unknown schema, symlinked user config, unsafe permissions and unqualified gateway URLs.

**PR B — safe opt-in configuration:** If the installed CLI supports a secure key indirection, generate a **private, opt-in** provider containing only owner-managed fields and exact served LiteLLM alias from the shared registry. Otherwise, do not automate plaintext credentials: retain the launcher or provide an explicit owner-only, backup/preview-led manual UI step. Merge existing provider entries without replacing user-selected providers, existing Copilot subscription login or workspaces; no default switch under `make deploy`. Keep the canonical key at `~/.local/share/litellm/clients/copilot-cli.key` with private mode/ownership. Do not silently rotate it.

**PR C — runtime qualification:** With owner-approved local services, test `copilot --model <exact-qualified-alias>` (or installed native syntax), streaming, tools and failure/auth mode with a redacted scoped-key LiteLLM receipt. Independently check automatic **local** Ollama discovery and ensure it isn't confused with Ollama **Cloud**. Verify missing/expired key, 401/429, gateway unavailable, unsupported models and mode switch to the original login; no gateway→direct-paid fallback.

## Reproducible source verification

- Existing: `scripts/lib/tests/test_copilot_stat_portability.py`, `scripts/lib/tests/test_litellm_client_registry.py`, `scripts/lib/tests/test_gateway_orchestration.py`, `make check-cli-contract`, `make verify`.
- Add harness fixtures for exact installed `providers.json` native shape **once independently verified**, preserving unknown providers and avoiding secrets in logs. Test opt-in, gate-off, second deploy, symlink, mode-0600 key, wrong permissions, user-owned collisions, unknown alias and rollback in disposable homes.
- Record separate `configured`, `catalogue-qualified`, `live-client` and `owner-reported` rows in [capability-matrix.md](capability-matrix.md) when verified.

## Non-goals, rollback and owner-only checks

No editing `~/.copilot` in CI; no logging keys, public LiteLLM exposure, forcing all Copilot commands onto a local model or altering VS Code Copilot Chat. If native provider configuration cannot safely obtain credentials, preserve the existing launcher. Rollback is removal of only a named dotfiles-owned provider after comparison with a private backup, not deletion of the user's providers file. Real prompts, credentials and account trials remain owner-controlled.

## Status

**What this PR does:** records the sourced exact-native-vs-legacy decision, verification commands, safety constraints and handoff. **What this PR does not do:** change Copilot CLI provider settings or claim installed routing works. An implemented native configuration requires the verified schema/secret decision first. Keep #218 open.

## 2026-11-09 disposition (issue #218 closure)

**UNSUPPORTED (versioned)**: GitHub Copilot CLI 1.0.105 exposes `--model` selection only — no native `providers.json` BYOK/schema surface and no custom base-URL/provider option on this build; `~/.copilot/providers.json` does not exist. Per the gate contract (plaintext credentials, no `$ENV` expansion) no secret was written to test blindly. The working `scripts/copilot-litellm` launcher + scoped `copilot-cli.key` remain the operational path. Re-entrant: a BYOK-capable CLI release reopens this with the native-key test as the first task.
