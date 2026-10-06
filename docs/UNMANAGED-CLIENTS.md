# Unmanaged LiteLLM Clients

These clients are configured outside this repository. Use the gateway and a
client-specific virtual key to route requests through LiteLLM. Replace
`<gateway>` with the proxy URL reachable by that client, and use model aliases
configured by the gateway. See [LITELLM.md](LITELLM.md) for managed clients,
gateway setup, and key handling.

## Cursor

| What to set | Where | Value shape |
|-------------|-------|-------------|
| OpenAI Base URL override | Cursor Settings → Models → OpenAI → Override OpenAI Base URL | `<gateway>/cursor` |
| OpenAI API key | Same settings area | Cursor-specific virtual key |

**Caveat:** Cursor-originated requests come from Cursor servers, so the proxy
must be internet-reachable; a loopback-only proxy will not work. In particular,
the local loopback Caddy endpoint does not make a `127.0.0.1` proxy reachable
from Cursor's servers. Agent mode requires LiteLLM v1.97 or later. As an
alternative, configure Cursor's Azure OpenAI provider with the gateway base URL
without the `/cursor` suffix.

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
