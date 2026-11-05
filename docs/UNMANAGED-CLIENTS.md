# Unmanaged LiteLLM Clients

These clients are configured outside this repository. Use the gateway and a
client-specific virtual key to route requests through LiteLLM. Replace
`<gateway>` with the proxy URL reachable by that client, and use model aliases
configured by the gateway. See [LITELLM.md](LITELLM.md) for managed clients,
gateway setup, and key handling.

## Cursor

Dotfiles may provision a **dedicated virtual key** for the Cursor desktop GUI,
but never edits Cursor's own settings or exposes LiteLLM. On macOS, enable
`DOTFILES_RUN_LITELLM_SETUP=1`, `DOTFILES_USE_LITELLM_PROXY=1` and
`DOTFILES_RUN_CURSOR_SETUP=1` in the owner-managed environment, with the
production `Cursor.app` bundle installed. Re-run the managed LiteLLM setup;
the key is stored privately in
`~/.local/share/litellm/clients/cursor.key` (mode `0600`), and the
`LITELLM_CURSOR_KEY` entry is preserved in `service.env`. This creates a
LiteLLM virtual key named `cursor`, not a separate LiteLLM user record.

| What to set | Where | Value shape |
|-------------|-------|-------------|
| OpenAI Base URL override | Cursor Settings → Models → API Keys → Override OpenAI Base URL | `<approved HTTPS gateway>/cursor` (LiteLLM 1.97+, not `/v1` for the dedicated Cursor route) |
| OpenAI API key | Same settings area | Value of the private `cursor.key` file, entered manually |
| Custom model | Cursor Settings → Models → Add Custom Model | Exact public LiteLLM model alias that supports the requested mode |

**Network and security blocker:** LiteLLM's Cursor integration documents
server-originated traffic. The default local
`http://127.0.0.1:4000/v1` in the screenshot is therefore **not a
verified working Cursor endpoint**. Neither the private loopback service
nor a Tailscale-only address is reachable from Cursor's servers. Do **not**
publish the LiteLLM service, its administrative API or its master key to
the internet as part of key provisioning. A separately reviewed, authenticated
HTTPS access design (or independently proven local-only request route) is
required before declaring GUI inference functional.

After approved connectivity is available, manually enter the virtual key,
choose a verified LiteLLM alias, and test a local Chat request, an Agent
tool call and an authorized gateway usage receipt attributed to `cursor`.
Confirm the key fails for unrelated administrative operations and that
disabling the Cursor gate does not revoke or erase existing secrets.
Until then, the credential is provisioned but the Cursor transport is
**UNVERIFIED**.

The integration does **not** configure Cursor Tab, Auto, Cloud/Background
Agents, Automations, Cursor CLI (`agent`/`cursor-agent`) or Cursor's API/SDK.
`CURSOR_API_KEY` is a separate Cursor CLI credential and must not be replaced
with `LITELLM_CURSOR_KEY`. Only local Chat/Agent modes can be considered for
bring-your-own-key routing.

References: [LiteLLM Cursor integration](https://docs.litellm.ai/docs/tutorials/cursor_integration),
[Cursor BYOK](https://prod.cursor.com/help/models-and-usage/api-keys),
[OpenAI's Cursor gateway guidance](https://help.openai.com/en/articles/20001506-using-openai-models-in-cursor).

## Gemini CLI

| What to set | Where | Value shape |
|-------------|-------|-------------|
| `GOOGLE_GEMINI_BASE_URL` | Gemini CLI environment | `<gateway>` |
| `GEMINI_API_KEY` | Gemini CLI environment | Gemini-client virtual key |
| `model_group_alias` | LiteLLM gateway configuration | Map requested Gemini model names to gateway model groups, including non-Gemini upstreams |

**Caveat:** Set the aliases on the gateway for any non-Gemini upstream model
you want Gemini CLI to use; the client environment variables alone do not map
model names.

## Claude Desktop Cowork

| What to set | Where | Value shape |
|-------------|-------|-------------|
| Gateway URL | Cowork custom model provider settings | Gateway host/base URL only; do not append `/v1` |
| API key | Same provider settings | Cowork-specific bearer virtual key |

**Caveat:** Cowork discovers models with `GET /v1/models` and sends inference
with `POST /v1/messages`. Its model picker filters for IDs containing `claude`
or `anthropic` unless `inferenceModels` is set.

## VS Code GitHub Copilot

Choose one of these best-effort integration paths:

| What to set | Where | Value shape |
|-------------|-------|-------------|
| LiteLLM VS Code extension gateway URL | Extension settings | `<gateway>` |
| LiteLLM VS Code extension API key | Extension sign-in/settings; stored in VS Code secret storage | Copilot-client virtual key |
| `github.copilot.advanced.debug.overrideProxyUrl` | VS Code settings JSON | `<gateway>` |
| `github.copilot.advanced.debug.testOverrideProxyUrl` | VS Code settings JSON | `<gateway>` |

**Caveat:** The extension path is the direct gateway integration. The Copilot
proxy-override settings are debug overrides, are best-effort, and require a
GitHub Copilot subscription.

## Attribution

Use a separate virtual key for each client. LiteLLM then attributes spend to
the client key, making per-client usage distinguishable.
