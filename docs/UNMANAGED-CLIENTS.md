# Unmanaged LiteLLM Clients

These clients are configured outside this repository or require a separate owner opt-in. The presence of a provisioned virtual key does **not** prove that the actual client selected LiteLLM. Replace `<gateway>` with a URL reachable by the *actual requesting process*, use exact served gateway aliases, and preserve user-owned settings and existing provider defaults. See [LITELLM.md](LITELLM.md) for gateway/key handling and the [modernization roadmap](MODERNIZATION_ROADMAP.md) for current tool-family work.

## Cursor

Dotfiles may provision a **dedicated virtual key** for the Cursor desktop GUI, but never edits Cursor's own settings or exposes LiteLLM. On macOS, enable `DOTFILES_RUN_LITELLM_SETUP=1`, `DOTFILES_USE_LITELLM_PROXY=1` and `DOTFILES_RUN_CURSOR_SETUP=1` in the owner-managed environment, with the production `Cursor.app` bundle installed. Re-run the managed LiteLLM setup; the key is stored privately in `~/.local/share/litellm/clients/cursor.key` (mode `0600`), and the `LITELLM_CURSOR_KEY` entry is preserved in `service.env`. This creates a LiteLLM virtual key named `cursor`, not a separate LiteLLM user record.

| What to set | Where | Value shape |
|-------------|-------|-------------|
| OpenAI Base URL override | Cursor Settings → Models → API Keys → Override OpenAI Base URL | `<approved HTTPS gateway>/cursor` (LiteLLM 1.97+, not `/v1` for the dedicated Cursor route) |
| OpenAI API key | Same settings area | Value of the private `cursor.key` file, entered manually |
| Custom model | Cursor Settings → Models → Add Custom Model | Exact public LiteLLM model alias that supports the requested mode |

**Network and security blocker:** LiteLLM's Cursor integration documents server-originated traffic. The default local `http://127.0.0.1:4000/v1` in the screenshot is therefore **not a verified working Cursor endpoint**. Neither the private loopback service nor a Tailscale-only address is reachable from Cursor's servers. Do **not** publish the LiteLLM service, its administrative API or its master key to the internet as part of key provisioning. A separately reviewed, authenticated HTTPS access design (or independently proven local-only request route) is required before declaring GUI inference functional.

After approved connectivity is available, manually enter the virtual key, choose a verified LiteLLM alias, and test a local Chat request, an Agent tool call and an authorized gateway usage receipt attributed to `cursor`. Confirm the key fails for unrelated administrative operations and that disabling the Cursor gate does not revoke or erase existing secrets. Until then, the credential is provisioned but the Cursor transport is **UNVERIFIED**.

The integration does **not** configure Cursor Tab, Auto, Cloud/Background Agents, Automations, Cursor CLI (`agent`/`cursor-agent`) or Cursor's API/SDK. `CURSOR_API_KEY` is a separate Cursor CLI credential and must not be replaced with `LITELLM_CURSOR_KEY`. Only client-supported Ask/Plan/Agent modes can be considered for bring-your-own-key routing. **Cursor CLI has a separate provider/auth contract from Cursor Desktop**; both are in [#219](https://github.com/randytarampi/dotfiles/issues/219), and a Cursor server-originated request must not be given a public gateway merely to satisfy test connectivity.

References: [LiteLLM Cursor integration](https://docs.litellm.ai/docs/tutorials/cursor_integration), [Cursor BYOK](https://prod.cursor.com/help/models-and-usage/api-keys), [OpenAI's Cursor gateway guidance](https://help.openai.com/en/articles/20001506-using-openai-models-in-cursor).

## Gemini CLI

| What to set | Where | Value shape |
|-------------|-------|-------------|
| `GOOGLE_GEMINI_BASE_URL` | Gemini CLI environment | `<gateway>` |
| `GEMINI_API_KEY` | Gemini CLI environment | Gemini-client virtual key |
| `model_group_alias` | LiteLLM gateway configuration | Map requested Gemini model names to gateway model groups, including non-Gemini upstreams |

**Caveat:** Set the aliases on the gateway for any non-Gemini upstream model you want Gemini CLI to use; the client environment variables alone do not map model names. The native Gemini wire, installed CLI version, key scope, account auth precedence and real gateway receipts need separate qualification ([#210](https://github.com/randytarampi/dotfiles/issues/210), [LiteLLM Gemini CLI](https://docs.litellm.ai/docs/tutorials/litellm_gemini_cli)). This does not modify the separately pinned Gemini GitHub reviewer.

## Copilot CLI (distinct from VS Code Copilot Chat)

[GitHub's Copilot CLI BYOK guide](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-llm-models) now documents `~/.copilot/providers.json`, `COPILOT_PROVIDERS_CONFIG`, OpenAI-compatible and Anthropic model providers, and local Ollama discovery. **Credentials in `providers.json` are literal plaintext**; environment-looking strings are not interpolated. The existing `scripts/copilot-litellm` launcher is a separate opt-in mechanism, not proof of native model selection. [#218](https://github.com/randytarampi/dotfiles/issues/218) will qualify a safe private credential strategy, exact served model, tools/streaming, owner-owned profiles and gateway receipts without altering Copilot's existing login.

## Claude Desktop Cowork

| What to set | Where | Value shape |
|-------------|-------|-------------|
| Gateway URL | Cowork custom model provider settings | Gateway host/base URL only; do not append `/v1` |
| API key | Same provider settings | Cowork-specific bearer virtual key |

**Caveat:** Cowork discovers models with `GET /v1/models` and sends inference with `POST /v1/messages`. Its model picker filters for IDs containing `claude` or `anthropic` unless `inferenceModels` is set. See the [official LiteLLM Desktop & Cowork integration](https://docs.litellm.ai/docs/proxy/client_setup/claude_desktop). A dedicated Desktop key is separate from Claude CLI; Chat, Cowork and Code must be qualified independently ([#209](https://github.com/randytarampi/dotfiles/issues/209)).

## VS Code GitHub Copilot

Choose one of these best-effort integration paths:

| What to set | Where | Value shape |
|-------------|-------|-------------|
| LiteLLM VS Code extension gateway URL | Extension settings | `<gateway>` |
| LiteLLM VS Code extension API key | Extension sign-in/settings; stored in VS Code secret storage | Copilot-client virtual key |
| `github.copilot.advanced.debug.overrideProxyUrl` | VS Code settings JSON | `<gateway>` |
| `github.copilot.advanced.debug.testOverrideProxyUrl` | VS Code settings JSON | `<gateway>` |

**Caveat:** The extension path is an independent gateway integration candidate. The Copilot proxy-override settings are **debug overrides**, are best-effort and require a GitHub Copilot subscription; the [LiteLLM tutorial](https://docs.litellm.ai/docs/tutorials/github_copilot_integration#benefits-of-using-vs-code-chat-with-litellm) does **not** guarantee a stable supported production BYOK path in every installed extension version. [#220](https://github.com/randytarampi/dotfiles/issues/220) must qualify the actual VS Code/Copilot Chat model provider or extension separately from Copilot CLI #218.

## Attribution

Use a separate virtual key for each client. LiteLLM then attributes spend to the client key, making per-client usage distinguishable.
