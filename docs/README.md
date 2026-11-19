# Documentation index

Every guide in this directory, grouped by what you are trying to do. Start with
[install.md](install.md) if you are setting a machine up, and [conventions.md](conventions.md)
if you are changing the repository.

This index is the navigation root. `make check-docs-drift` fails if a document here cannot be
reached from `README.md` or `AGENTS.md` through some chain of links, so a new guide belongs in
this file (or linked from one that is).

## Set up a machine

- [install.md](install.md) — installation, upgrade and verification flow
- [clean-bootstrap.md](clean-bootstrap.md) — the L0–L3 support contract and phase acceptance
- [bootstrap-acceptance-receipts.md](bootstrap-acceptance-receipts.md) — privacy-bounded host evidence
- [bootstrap-host-capabilities.md](bootstrap-host-capabilities.md) — read-only OS/CPU capability report
- [linux-brewfile-eligibility.md](linux-brewfile-eligibility.md) — what is eligible on Linux, per manifest

## Repository conventions and structure

- [conventions.md](conventions.md) — CLI contract, env taxonomy, Markdown rules, base-URL overrides
- [orchestration.md](orchestration.md) — three-layer architecture, script inventory, gates
- [adding.md](adding.md) — adding a tier, MCP server, configure script, gate or hash trigger
- [modernization-roadmap.md](modernization-roadmap.md) — live contract, current programme, open decisions
- [telemetry.md](telemetry.md) — per-tool telemetry opt-outs and the fleet registry

## Models and routing

- [tiers.md](tiers.md) — tier definitions, role tables, fallback chains, Ollama Cloud models
- [model-updates.md](model-updates.md) — model registry maintenance and verification sequence
- [litellm.md](litellm.md) — gateway configuration, client keys, spend and OAuth
- [meridian.md](meridian.md) — subscription proxy, SDK feature toggles, context sync
- [strict-free-admission.md](strict-free-admission.md) — the E1.0 zero-charge admission policy
- [strict-free-executor.md](strict-free-executor.md) — the E1.1 executor contract
- [strict-free-executor-inventory.md](strict-free-executor-inventory.md) — interception-point inventory

## Local inference

- [omlx.md](omlx.md) — oMLX service, wired limits, KV settings
- [ollama-service.md](ollama-service.md) — the Ollama daemon and its environment
- [optional-inference.md](optional-inference.md) — optional local inference paths
- [local-service-readiness.md](local-service-readiness.md) — anonymous loopback readiness audit
- [local-speech-acceptance.md](local-speech-acceptance.md) — STT/TTS acceptance rows
- [local-utility-models.md](local-utility-models.md) — bounded benchmark ledger for small local models

## Agents and clients

- [capability-matrix.md](capability-matrix.md) — per-tool providers, MCP, ACP, skills, guidance
- [opencode.md](opencode.md) — OpenCode configuration, ACP verification, Tokenscope
- [pi.md](pi.md) — Pi CLI, providers, MCP, ACP, skills
- [junie.md](junie.md) — Junie model groups and profile generation
- [cortex.md](cortex.md) — Snowflake Cortex Code specialist
- [context-mode.md](context-mode.md) — the opt-in Context Mode integration
- [dcp.md](dcp.md) — dynamic context pruning thresholds
- [multiplexer.md](multiplexer.md) — tmux/zellij side-by-side editing
- [chatgpt-desktop.md](chatgpt-desktop.md) — Codex inside ChatGPT Desktop, and its limits
- [claude-desktop.md](claude-desktop.md) — Claude Desktop gateway route
- [unmanaged-clients.md](unmanaged-clients.md) — Cursor, Gemini CLI, Cowork, VS Code Copilot
- [copilot-cli-gateway-acceptance.md](copilot-cli-gateway-acceptance.md) — Copilot CLI acceptance (unsupported)
- [cursor-gateway-acceptance.md](cursor-gateway-acceptance.md) — Cursor acceptance (unsupported)
- [vscode-gateway-acceptance.md](vscode-gateway-acceptance.md) — VS Code acceptance
- [xcode-mcp.md](xcode-mcp.md) — Xcode MCP bridge

## Services, remote access and memory

- [caddy.md](caddy.md) — Caddy, LAN exposure, certificates
- [remote-access.md](remote-access.md) — remote access routes and boundaries
- [chat-frontend.md](chat-frontend.md) — Open WebUI reconciler and deployment wiring
- [voice.md](voice.md) — voice plugin, tier-aware STT/TTS
- [basic-memory.md](basic-memory.md) — shared MCP memory
- [paseo-security.md](paseo-security.md) — Paseo security model and owner-only runbook
- [paseo-acp.md](paseo-acp.md) — Paseo ACP provider registration
- [paseo-mcp.md](paseo-mcp.md) — Paseo MCP service activation
- [paseo-voice.md](paseo-voice.md) — Paseo speech routes
- [paseo-metadata.md](paseo-metadata.md) — Paseo metadata generation
- [paseo-plugins.md](paseo-plugins.md) — Paseo plugin surface
- [paseo-litellm.md](paseo-litellm.md) — Paseo through the gateway
- [paseo-remote.md](paseo-remote.md) — Paseo remote/hosted relay checks
- [hermes-paseo.md](hermes-paseo.md) — Hermes selection inside Paseo

## Review automation and history

- [agentic-review.md](agentic-review.md) — agentic PR review workflows, labels, trusted refs
- [historical-attribution-migration.md](historical-attribution-migration.md) — the contribution-attribution migration record
