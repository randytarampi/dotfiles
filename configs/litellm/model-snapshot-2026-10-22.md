# Model catalogue snapshot — 2026-10-22

**Snapshot captured at:** 2026-10-23 00:05 CEST, from the checked-out repository. This is a point-in-time configuration capture for PR E review; it does not assert live provider availability. **Filename provenance:** `2026-10-22` in the filename is the planning-cycle date the snapshot was commissioned; the operational capture/probe timestamps in these documents are the authoritative times. **Live provider probes:** see the separate probe dates in [the E2 provider spike findings](../../docs/e2-cohere-poolside-spike-2026-10-22.md); those observations are not part of this snapshot.

## Oh My OpenCode preset roles

The table records each preset's orchestrator model (primary) and fixer model (faster), as configured in `configs/opencode/oh-my-opencode-slim.json`.

| Preset | Primary (orchestrator) | Faster (fixer) |
|---|---|---|
| pro | ollama-cloud/glm-5.3-flash | ollama-cloud/kimi-k2.7-code |
| pro-plus | ollama-cloud/glm-5.3-flash | openai/gpt-6-luna |
| pro-plus-anthropic | anthropic/claude-sonnet-5-5 | openai/gpt-6-luna |
| anthropic | anthropic/claude-sonnet-5-5 | anthropic/claude-haiku-5-5 |
| plus | openai/gpt-6.1-sol | openai/gpt-6-luna |
| plus-anthropic | openai/gpt-6.1-sol | anthropic/claude-haiku-5-5 |
| local | _local:code-gen | _local:code-gen |
| local-quiet | _local:code-gen | _local:code-gen |
| local-loud | _local:code-gen | _local:code-gen |
| local-solo | _local:solo | _local:solo |
| omo-slim-openai | openai/gpt-6.1-sol | openai/gpt-6-luna |
| omo-slim-thirty-dollars | openai/gpt-6.1-sol | openai/gpt-6-luna |
| omo-slim-opencode-zen-free | opencode/muse-spark-1.3-contributor-free | opencode/nemotron-3.5-lightning-free |
| free | opencode/big-pickle | opencode/nemotron-3.5-lightning-free |

## Catalogue counts

Counts are entries under each file's `models` object:

| File | Entries |
|---|---:|
| `configs/opencode/opencode-models.json` | 7 |
| `configs/opencode/google-models.json` | 8 |
| `configs/opencode/openrouter-models.json` | 1 |

## LiteLLM model-rate data

Provenance (`configs/litellm/model-rates.json`): “Snapshot of LiteLLM's upstream model cost map, fetched 2026-10-09 via litellm.model_cost in the managed LiteLLM venv. Haiku 5.5 cache rates verified 2026-10-13 against https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json. Covers wire families missing from the installed package's bundled backup file (claude-* entries). Refresh when model families churn; free, subscription (chatgpt/*) and local rows stay unpriced by design.”

Model keys: `gpt-6-astra`, `gpt-6-astra-flex`, `gpt-6-astra-fast`, `gpt-6-luna`, `gpt-6-luna-flex`, `gpt-6-luna-fast`, `gpt-6.1-sol-flex`, `gpt-6.1-sol-fast`, `claude-fable-5-1`, `claude-haiku-4-5`, `claude-haiku-5-5`, `claude-opus-4-6`, `claude-opus-4-8`, `claude-opus-5`, `claude-opus-5-5`, `claude-sonnet-4-6`, `claude-sonnet-5`, `claude-sonnet-5-5`.
