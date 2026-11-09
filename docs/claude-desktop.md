# Claude Desktop third-party LiteLLM gateway pilot

**Status (reconciled 2026-11-07):** repo-side Desktop helper/profile scaffold delivered; **actual Desktop Chat, Cowork and Code gateway outcomes still UNKNOWN**. Owner reports other Claude tooling working recently, which is not Desktop-specific acceptance. [#209](https://github.com/randytarampi/dotfiles/issues/209) is the dedicated priority Desktop issue. The existing `scripts/claude-litellm` is for standalone Claude CLI and must not be conflated with the GUI.

## Opt-in contract

Anthropic documents a developer-mode gateway configuration UI and an optional managed profile. Gateways must implement `POST /v1/messages` with streaming and tools; the optional `GET /v1/models` is replaced by explicit model IDs in the profile. Model identity, cache fields, compaction and tool fidelity are not guaranteed merely by exposing a LiteLLM OpenAI-compatible route.

Sources:
- https://claude.com/docs/third-party/claude-desktop/gateway
- https://claude.com/docs/third-party/claude-desktop/installation#single-machine-setup
- https://claude.com/docs/third-party/claude-desktop/configuration-format
- https://docs.litellm.ai/docs/proxy/client_setup/claude_desktop (LiteLLM-specific virtual-key and MCP setup; runtime evidence remains owner-gated)

## Owner-controlled local procedure

1. Record the installed macOS and Claude Desktop versions, current deployment
    mode, and effective settings. Preserve user-owned Desktop history/config.
2. Separately issue an **owner-authorised** dedicated LiteLLM virtual key.
    This PR deliberately does **not** register or automatically mint a new
    client alias. Place it at
    `~/.local/share/litellm/clients/claude-desktop.key` (mode 0600,
    owner-only, no symlinks or writable parent directories). Do not reuse the
    `claude.key` from the standalone CLI.
3. With an installed Python 3 interpreter and this checked-out source,
    `scripts/claude-desktop-credential-helper.py --check` should print only
    `Claude Desktop credential ready`. Without `--check` the helper prints
    the **secret** to stdout for Desktop; never run that form in logs or chats.
4. For the optional managed-profile route, make a **stable GUI-safe wrapper**
    with absolute executable paths. This is an explicitly manual owner action,
    not a dotfiles deployment step. Run from a persistent checkout, not a
    temporary worktree; the wrapper embeds the current resolved Python
    interpreter and helper source paths. **Trust boundary:** this procedure is
    intended only for a trusted single-user host. Inspect the wrapper's
    `~/.local/bin` directory and every parent of the helper's persistent
    checkout and interpreter; none may be writable by an untrusted local user,
    and the wrapper, helper and interpreter must remain owner-controlled.
    On a shared host, first deploy these executables under an independently
    secured, non-symlinked owner-controlled location. Otherwise, an attacker
    could replace executable code before it reads the bearer credential:

    ```sh
    mkdir -p "$HOME/.local/bin"
    ( set -C
      scripts/claude-desktop-credential-helper.py --wrapper \
        > "$HOME/.local/bin/claude-desktop-gateway-key"
    )
    chmod 700 "$HOME/.local/bin/claude-desktop-gateway-key"
    env -i HOME="$HOME" PATH=/usr/bin:/bin \
      "$HOME/.local/bin/claude-desktop-gateway-key" --check
    ```

    The wrapper must be tested from a restricted GUI-like environment: a
    terminal `--check` through `/usr/bin/env python3` does **not** prove
    Claude Desktop can locate Homebrew or an isolated interpreter. The
    generation command refuses to overwrite an existing wrapper; inspect
    and replace it manually when Python or the repository moves. Never invoke
    the unqualified wrapper without `--check` in diagnostic logs.
5. Preferred first pilot: Claude Desktop → Help → Troubleshooting → Enable
    Developer Mode → Developer → Configure Third-Party Inference. Choose Gateway,
    root `http://127.0.0.1:4000` (no `/v1`) and the explicit Static API key
    option, if available. Enter the dedicated key directly into that UI;
    **do not** export a plaintext key to a repo-controlled profile. Apply,
    Save & Restart, and use the documented third-party sign-in path.
6. Optional managed-profile pilot, only with separate consent: generate a
    **secret-free** profile, specifying exact Messages-compatible aliases
    and the absolute path to the validated, stable wrapper:

    ```sh
    scripts/export-claude-desktop-gateway.py \
      --endpoint http://127.0.0.1:4000 \
      --model '<verified-served-messages-alias>' \
      --helper "$HOME/.local/bin/claude-desktop-gateway-key" \
      > claude-desktop.mobileconfig
    ```

    Inspect the profile and helper path before installing it. The exporter
    does not install anything or prove the installed Desktop build accepts it.
    Managed preferences may override user-local settings.

## P0 installed-consumer evidence ledger (#209)

**As of 2026-11-07: UNSUPPORTED is not yet established and the actual Chat, Cowork and Code LiteLLM paths remain UNKNOWN.** The exported profile, private helper and passing fixture tests are useful source evidence, not proof the installed application has selected the gateway. Owner consent is required before installing a profile, entering a dedicated credential or performing real Desktop tasks; no separate successful Desktop Chat/Cowork/Code requests have been reported.

For the first acceptance session, prefer the vendor-supported **Developer Mode → Configure Third-Party Inference → Gateway / Static API key** UI over installing a generated managed profile. Keep the base URL at the local root (no trailing /v1), use a separately scoped Desktop virtual key and a currently served model supporting Anthropic Messages tools and streaming. Under the current [LiteLLM Desktop guide](https://docs.litellm.ai/docs/proxy/client_setup/claude_desktop), the discovered picker may filter model IDs without claude/anthropic; explicit inferenceModels can supply the exact served aliases instead. Do not silently rename aliases or claim Chat Completions support proves Messages support.

| Surface | Required live receipt and negative case | Status on this PR |
|---|---|---|
| Desktop Chat | Selected third-party deployment, model, streamed multi-turn message, key usage receipt; bad key and gateway-down fail visibly | UNKNOWN |
| Desktop Cowork | Tools and follow-up, permissions/prompt cache; rejected tool and failed gateway must not silently use hosted account | UNKNOWN |
| Desktop Code session | Same deployed endpoint confirmed on a real Code task with correct model/tool schema; restart and switch-back | UNKNOWN |
| Optional LiteLLM MCP connector | Owner-selected read-only tool permission and correct connector identity; unauthorised tool denied | NOT REQUESTED |

**Evidence record for the implementer:** installed app build, gateway build, exact alias and protocol (never token value), model picker/display, UI deployment selection, redacted request ID and per-key attribution, streaming/tool outcome, wrong-key/offline negative result, rollback and whether observed directly or owner-reported. Redact diagnostic exports before GitHub publication.

**Safe stopping points:** if the app version does not support loopback HTTP, the selected model has no native Messages support, the GUI ignores the dedicated key, or switching modes risks user history, stop with a precise UNKNOWN/UNSUPPORTED state. Do not patch Desktop internals, add a public tunnel, install a profile automatically or reuse the CLI credential. The source-only steps in this PR do not require a host restart.

## Acceptance and negative cases (owner-only)

- Capture actual Desktop version, endpoint/model selection, private-key readback (identity/permissions only), and a gateway request receipt.
- Chat: streaming, multi-turn turns and model-picker mapping.
- Cowork and Code: tool calls, follow-up, prompt-cache/usage fidelity, errors, model changes and effective context.
- Negative cases: absent/unsafe/rotated key, symlinked directory, unsupported served alias, gateway down, failure handling, hosted-mode return path, no changes to other Claude profiles, sessions, or client state.
- Keep install and rollback manual; a helper test is not live transport proof. Classify every result VERIFIED / OWNER-REPORTED / NOT RUN / UNKNOWN.
- Diagnostic report: Help → Troubleshooting → Generate Diagnostic Report; redact secrets before placing any evidence in a PR.

**Rollback:** restore the original Desktop deployment mode/settings and remove a test managed profile only with explicit consent. Never delete key material or local history automatically.

**Review/maintenance:** confirm current Anthropic configuration field names, test exact executable path in Desktop's GUI process context, inspect scope/ permissions, run focused tests + `make verify`, check current-head CI, then perform the separately authorised owner-host acceptance. No issue #82 closure is claimed.

**Attribution:** drafted by ChatGPT (GPT-6), 2026-11-03. No external review, owner-machine tests, credential issuance, or GUI configuration were performed.

## 2026-11-09 gateway wiring + panel blocker (issue #209 state)

**#209 remains OPEN.** The owner session wrote a gateway URL and scoped-key reference to the native `claude_desktop_config.json`, retaining a private backup and existing `mcpServers`, and relaunched the app. An **independent gateway-wire** request using the dedicated key completed at `POST /v1/messages` (`meridian/claude-haiku-5-5`, READY, usage/cache fields). This is **not** a successful Desktop Chat, Cowork or Code inference receipt. The installed application's **Configure Third-Party Inference** panel reports `Couldn't load configuration` and `Couldn't update saved configurations (×3)`. The next bounded step is to inspect the exact installed app build, native schema precedence and a **redacted** GUI diagnostic (no credentials or chat history), correct only a verified schema or ownership mismatch, then run separate user-authorised Chat/Cowork/Code tasks with wrong-key, offline-gateway and hosted-mode rollback cases. Do not infer that writing the file alone proves supported native GUI configuration.
