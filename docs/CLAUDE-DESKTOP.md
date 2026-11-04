# Claude Desktop third-party LiteLLM gateway pilot

**Status: repo-side scaffold only; owner host and live inference NOT RUN.**
The existing `scripts/claude-litellm` is for standalone Claude CLI, not Desktop.

## Opt-in contract

Anthropic documents a developer-mode gateway configuration UI and an optional
managed profile. Gateways must implement `POST /v1/messages` with streaming
and tools; the optional `GET /v1/models` is replaced by explicit model IDs in
the profile. Model identity, cache fields, compaction and tool fidelity are not
guaranteed merely by exposing a LiteLLM OpenAI-compatible route.

Sources:
- https://claude.com/docs/third-party/claude-desktop/gateway
- https://claude.com/docs/third-party/claude-desktop/installation#single-machine-setup
- https://claude.com/docs/third-party/claude-desktop/configuration-format

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

## Acceptance and negative cases (owner-only)

- Capture actual Desktop version, endpoint/model selection, private-key
  readback (identity/permissions only), and a gateway request receipt.
- Chat: streaming, multi-turn turns and model-picker mapping.
- Cowork and Code: tool calls, follow-up, prompt-cache/usage fidelity,
  errors, model changes and effective context.
- Negative cases: absent/unsafe/rotated key, symlinked directory, unsupported
  served alias, gateway down, failure handling, hosted-mode return path,
  no changes to other Claude profiles, sessions, or client state.
- Keep install and rollback manual; a helper test is not live transport proof.
  Classify every result VERIFIED / OWNER-REPORTED / NOT RUN / UNKNOWN.
- Diagnostic report: Help → Troubleshooting → Generate Diagnostic Report;
  redact secrets before placing any evidence in a PR.

**Rollback:** restore the original Desktop deployment mode/settings and
remove a test managed profile only with explicit consent. Never delete key
material or local history automatically.

**Review/maintenance:** confirm current Anthropic configuration field names,
test exact executable path in Desktop's GUI process context, inspect scope/
permissions, run focused tests + `make verify`, check current-head CI, then
perform the separately authorised owner-host acceptance. No issue #82 closure
is claimed.

**Attribution:** drafted by ChatGPT (GPT-6), 2026-11-03. No external review,
owner-machine tests, credential issuance, or GUI configuration were performed.
