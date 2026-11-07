# VS Code local and gateway inference acceptance

> P1/P2 [#220](https://github.com/randytarampi/dotfiles/issues/220), main@ab5077e30 (2026-11-07). This is an **actionable protocol-selection and implementation plan**, not installed VS Code acceptance.

## Consumer isolation

VS Code is an editor, not a single inference client. It may host GitHub Copilot Chat/Agent, a separate LiteLLM extension, local model providers, OpenCode-compatible extensions, MCP tools, and native editor features. A `LITELLM_VSCODE_KEY` provisioned by `scripts/lib/litellm_config.py` only authenticates a service client; it does **not** automatically configure an extension or intercept every editor request.

The [LiteLLM GitHub Copilot tutorial](https://docs.litellm.ai/docs/tutorials/github_copilot_integration#benefits-of-using-vs-code-chat-with-litellm) illustrates `github.copilot.advanced.debug.overrideProxyUrl` and `debug.testOverrideProxyUrl`. These are **debug overrides**, not a reliable production BYOK contract for every Copilot Chat/VS Code release. Treat use as an experiment only, never a global managed default.

## Phase A: inventory and select one supported consumer

On the installed host, record VS Code build/edition, GitHub Copilot Chat version, installed LiteLLM/local-model extensions, actual provider picker options, model/tool/MCP support and credential storage rules. Determine which client process originates inference requests and what it requires for authenticated loopback. Prefer a currently documented stable BYOK model provider in VS Code Chat/Agent where available; otherwise consider the dedicated LiteLLM extension with its own supported settings. If neither provides a supported integration, classify Copilot Chat native proxy **UNSUPPORTED** and preserve the subscription route.

Do not confuse Copilot CLI's native `providers.json` (#218) with VS Code's provider configuration or GitHub.com Copilot service.

## Phase B: bounded implementation

Implement only the chosen, verified supported extension's actual model/provider schema. Source the exact served model alias from the existing LiteLLM registry; use a dedicated `vscode.key` through VS Code **SecretStorage** or supported keychain interface, not tracked `settings.json`, workspace configuration or a plaintext API-key string. Never write a default global user setting without a preview and opt-in. Preserve existing Copilot login, extensions, workspace config and model selection. Test handling of missing extension, missing credential, wrong alias, symlink/unowned file, disabled proxy gate and denied requested tool access.

Any editor MCP/Basic Memory work requires its own permission and transport checks: availability of an MCP server to the agent is not proof of tool execution or memory sharing.

## Phase C: actual consumer acceptance

From VS Code Chat/Agent (not a terminal `curl`), select the exact alias, run one allowed code/read operation, record model and redacted per-client LiteLLM receipt; separately test a tool call, error/auth timeout, gateway down, a 429 and no direct-paid/privacy-shifting fallback. If using a debug override, label it explicitly experimental and record the exact extension version, expected breakage and reversal path. Confirm both dedicated LiteLLM mode and original Copilot subscription mode continue working with opt-in/opt-out.

Test local Ollama through a supported model-provider integration independently from remote Ollama Cloud. No automatic downloads, new model registry, local daemon startup or public listeners.

## Source/CI and rollback

Source entry points: [`docs/unmanaged-clients.md`](unmanaged-clients.md), `scripts/lib/litellm_config.py`, `scripts/lib/litellm_client_auth.py`, `configs/mcp/global-mcps.json`, [`capability-matrix.md`](capability-matrix.md). Run `PYTHONPATH=scripts/lib pytest -q scripts/lib/tests/test_litellm_client_registry.py scripts/lib/tests/test_check_mcp_parity.py`, `make check-mcp-parity`, `make check-cli-contract`, `make verify`. After a real schema is chosen, add isolated tests for secret-storage token reference, no secret serialization, gate-off, idempotence, invalid provider, native UI precedence, malicious workspace settings, and rollback. Owner-host tests are consent-gated.

Rollback is removal of *only* the opted-in extension provider entry/SecretStorage reference, leaving Copilot login and other workspace settings intact. No integration code is authored in this initial protocol-selection PR, and no active user configuration is modified.

## Completion classification

Distinct verdicts are required for **VS Code Copilot Chat**, **LiteLLM VS Code extension**, **local Ollama provider**, **Agent/tools**, and **MCP/Basic Memory**. A successful extension demo does not qualify all five. Record `OWNER-REPORTED`, `CONSUMER-VERIFIED`, `UNKNOWN` or explicit `UNSUPPORTED` with version and evidence. Do not close #220 before its selected supported integration succeeds or the owner accepts a precise unsupported disposition.
