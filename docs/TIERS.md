# Model Tiers & Selection Strategy

> Deep reference for tier definitions, model classification, fallback chains, and variant policy.
> For the quick summary, see the Model Tiers section in [AGENTS.md](../AGENTS.md).

---

## Preset Switching

Use `/preset <name>` to switch models at runtime. **Runtime-safe fields only:**
- `model`, `temperature`, `variant`, `options`

Changing `prompt`, `skills`, `mcps`, or `displayName` requires an OpenCode restart. Define presets properly in `oh-my-opencode-slim.json` for persistent configuration.

---

## Tier Definitions

Eleven tiers defined in `scripts/configure-opencode-tier.py` (source of truth):

| Tier | Providers | Best For |
|------|-----------|----------|
| **pro** | Ollama Cloud (glm-5.2 orchestrator, nemotron-3-ultra council) | Daily coding, budget mode |
| **pro-plus** | Ollama Cloud + OpenAI (`gpt-5.5`) | General development |
| **pro-plus-anthropic** | Anthropic + Ollama Cloud + OpenAI | Heavy orchestration |
| **plus** | OpenAI only (`gpt-5.5`, `gpt-5.4-mini`) | OpenAI-first workflow |
| **plus-anthropic** | OpenAI + Anthropic (no Ollama Cloud) | OpenAI + Anthropic hybrid |
| **anthropic** | Anthropic only | Anthropic-first workflow |
| **local-pro** | Local Ollama (all 4 categories: reasoning, code-gen, lightweight, vision) | Power users with diverse local models |
| **local** | Local Ollama (reasoning + code-gen + lightweight + vision) | Balanced offline/air-gapped |
| **local-mini** | Local Ollama (code-gen + lightweight + vision) | Minimal model diversity |
| **local-nano** | Local Ollama (single code-gen model + vision) | Single-model systems |
| **local-solo** | Local Ollama (single omnicapable model) | Maximum per-request quality, single-model simplicity |

> [!NOTE]
> When both `OLLAMA_API_KEY` and `ANTHROPIC_API_KEY` are set (but not `OPENAI_API_KEY`), auto-detection returns `pro-plus-anthropic`. The tier name implies OpenAI is also present, but the preset works correctly without it — Ollama Cloud handles orchestrator and Anthropic handles oracle.

Cloud presets (pro, pro-plus, pro-plus-anthropic) use Ollama Cloud models including `nemotron-3-ultra`, `minimax-m3`, `glm-5.2`, `glm-5.1`, `kimi-k2.6`, `kimi-k2.7-code`, `deepseek-v4-pro`, `deepseek-v4-flash`. The `plus` preset uses OpenAI models exclusively. The `plus-anthropic` preset uses OpenAI and Anthropic models without Ollama Cloud. The `anthropic` preset uses only Anthropic models. The `local-pro` preset uses all four `_local:<category>` placeholders resolved at runtime. The `local` preset uses reasoning + code-gen + lightweight + vision for a balanced 3-party council. The `local-mini` preset reduces to code-gen + lightweight + vision. The `local-nano` preset uses a single code-gen model for all roles (except vision) with a 2+1 council. The `local-solo` preset uses a single omnicapable model (completion+thinking+tools+vision) for all roles, with council diversity from variants rather than different models.

### Anthropic Tier (`anthropic`)

Anthropic-only preset with no OpenAI or Ollama Cloud providers:

| Role | Model | Variant |
|------|-------|---------|
| orchestrator | `claude-opus-4-6` | — |
| oracle | `claude-opus-4-8` | xhigh |
| librarian | `claude-haiku-4-5` | low |
| explorer | `claude-haiku-4-5` | low |
| designer | `claude-sonnet-4-6` | medium |
| fixer | `claude-sonnet-4-6` | low |
| observer | `claude-haiku-4-5` | low |

Council agent is defined inside each preset's agent list; alpha `claude-opus-4-8`, beta `claude-sonnet-4-6`, gamma `claude-opus-4-6`. Empty fallback chains by default — local Ollama models are appended automatically unless `--no-local-fallbacks` is passed.

### Plus-Anthropic Tier (`plus-anthropic`)

OpenAI + Anthropic preset with no Ollama Cloud providers:

| Role | Model | Variant |
|------|-------|---------|
| orchestrator | `openai/gpt-5.5` | — |
| oracle | `anthropic/claude-opus-4-8` | xhigh |
| librarian | `openai/gpt-5.4-nano` | low |
| explorer | `anthropic/claude-haiku-4-5` | low |
| designer | `anthropic/claude-sonnet-4-6` | medium |
| fixer | `openai/gpt-5.4-mini` | high |
| observer | `anthropic/claude-haiku-4-5` | low |

Council agent is defined inside each preset's agent list; alpha `claude-opus-4-8`, beta `gpt-5.5`, gamma `gpt-5.4`. Fallback chains mix OpenAI + Anthropic models per role — local Ollama models are appended automatically unless `--no-local-fallbacks` is passed.

### Local-Pro Tier (`local-pro`)

Fully offline preset using all four `_local:<category>` placeholders:

| Role | Placeholder | Resolves to |
|------|------------|-------------|
| orchestrator | `_local:code-gen` | Best local code-gen model |
| oracle | `_local:reasoning` | Best local reasoning model |
| librarian | `_local:lightweight` | Best local lightweight model |
| explorer | `_local:lightweight` | Best local lightweight model |
| designer | `_local:code-gen` | Best local code-gen model |
| fixer | `_local:code-gen` | Best local code-gen model |
| observer | `_local:vision` | Best local vision-capable lightweight model |

Council: α `_local:reasoning` high, β `_local:reasoning_2` high, γ `_local:reasoning_3` high. Best for power users with diverse local models spanning all four categories.

### Local Tier (`local`)

Balanced offline preset using reasoning + code-gen + lightweight + vision:

| Role | Placeholder | Resolves to |
|------|------------|-------------|
| orchestrator | `_local:code-gen` | Best local code-gen model |
| oracle | `_local:reasoning` | Best local reasoning model |
| librarian | `_local:lightweight` | Best local lightweight model |
| explorer | `_local:lightweight` | Best local lightweight model |
| designer | `_local:code-gen` | Best local code-gen model |
| fixer | `_local:code-gen` | Best local code-gen model |
| observer | `_local:vision` | Best local vision-capable lightweight model |

Council: α `_local:reasoning` high, β `_local:code-gen` high, γ `_local:lightweight` high. Best for balanced offline use with 3-party council diversity across model categories.

### Local-Mini Tier (`local-mini`)

Minimal-diversity preset using code-gen + lightweight + vision:

| Role | Placeholder | Resolves to |
|------|------------|-------------|
| orchestrator | `_local:code-gen` | Best local code-gen model |
| oracle | `_local:code-gen` | Best local code-gen model |
| librarian | `_local:lightweight` | Best local lightweight model |
| explorer | `_local:lightweight` | Best local lightweight model |
| designer | `_local:code-gen` | Best local code-gen model |
| fixer | `_local:code-gen` | Best local code-gen model |
| observer | `_local:vision` | Best local vision-capable lightweight model |

Council: α `_local:code-gen` high, β `_local:lightweight` high, γ `_local:vision` high. Best for systems with only code-gen and lightweight models available.

### Local-Nano Tier (`local-nano`)

Single-model preset using one code-gen model for all roles (except vision):

| Role | Placeholder | Resolves to |
|------|------------|-------------|
| orchestrator | `_local:code-gen` | Best local code-gen model |
| oracle | `_local:code-gen` | Best local code-gen model |
| librarian | `_local:code-gen` | Best local code-gen model |
| explorer | `_local:code-gen` | Best local code-gen model |
| designer | `_local:code-gen` | Best local code-gen model |
| fixer | `_local:code-gen` | Best local code-gen model |
| observer | `_local:vision` | Best local vision-capable lightweight model |

Council: α `_local:code-gen` high, β `_local:lightweight` high, γ `_local:vision` high. Best for single-model systems — council uses the code-gen model plus lightweight and vision for diversity.

### Local-Solo Tier (`local-solo`)

Single-model preset using one omnicapable model for all roles:

| Role | Placeholder | Resolves to |
|------|------------|-------------|
| orchestrator | `_local:solo` | Best local solo model |
| oracle | `_local:solo` | Best local solo model |
| librarian | `_local:solo` | Best local solo model |
| explorer | `_local:solo` | Best local solo model |
| designer | `_local:solo` | Best local solo model |
| fixer | `_local:solo` | Best local solo model |
| observer | `_local:solo` | Best local solo model |

Council: α `_local:solo` max, β `_local:solo` high, γ `_local:solo` high. Diversity comes from variants, not different models. If no solo model exists, falls back to code-gen + vision (local-nano behavior).

> [!NOTE]
> Solo models require all four capabilities: completion + thinking + tools + vision. This maximizes per-request quality but needs enough VRAM. Users with limited VRAM should use local-mini or local-nano.

---

## Local Model Classification

Placeholders are resolved by `configure-opencode-tier.py` using model name heuristics, size rules, `ollama show` parameter counts, and capability-aware classification:
- **reasoning**: models containing `r1`, `reasoning`, `deep-think`, `think`, `qwq`, `reflection`
- **code-gen**: models containing `coder`, `code`, `coding`, `devstral`, `codestral`, `deepseek-coder`, `qwen2.5-coder`, `qwen3-coder`, `codeqwen`
- **lightweight**: models containing `mini`, `small`, `tiny`, `phi`, `gemma:2`, `gemma3`, `smol`
- **vision**: subset of `lightweight` models that also have the `vision` capability (from `ollama show`)
- **solo**: models with all four capabilities (`completion` + `thinking` + `tools` + `vision`), purely capability-based (no name heuristics), sorted by parameter count descending

Indexed placeholders (`_local:<category>_2`) resolve to the second-best model in a category, ensuring council diversity. For example, `_local:code-gen_2` gives a different model from `_local:code-gen` when multiple code-gen models are available, or falls back to the second-best reasoning model if code-gen only has one entry.

Additional classification rules (applied after name heuristics):
- **Size rule**: models with `ollama list` SIZE < 12 GB are classified as `lightweight`
- **`ollama show` parameter-based**: unclassified models (≥ 12 GB, no name heuristic match) are classified via `ollama show` parameter count — parameters ≥ 7B → reasoning, parameters < 7B → code-gen (not lightweight)
- **Capability filtering**: after initial classification, each category is filtered by required capabilities parsed from `ollama show`:
  - `reasoning` requires `thinking` + `tools`
  - `code-gen`: name-heuristic-qualified models bypass capability checks; models classified via size/fallback rules require `thinking` + `completion`
  - `lightweight` requires `tools`
  - `vision` requires `tools` + `vision` (subset of lightweight)
  - `solo` requires `completion` + `thinking` + `tools` + `vision` (no name heuristics)
- **Code-gen reuse**: if no code-gen model is found via name heuristic, the reasoning model is reused for code-gen roles
- **Vision fallback**: if no vision-capable model exists, the best lightweight model is used with a warning
- **Indexed placeholders**: `_local:<category>_2` resolves to the second-best model in a category (e.g., `_local:code-gen_2` for council gamma diversity)

**Runtime warnings**: `configure-opencode-tier.py` warns when council councillors resolve to the same model (limited diversity), and reports total distinct models available across categories.

Switch tier: `scripts/configure-opencode-tier.py` <tier> (pro, pro-plus, pro-plus-anthropic, plus, plus-anthropic, anthropic, local-pro, local, local-mini, local-nano, local-solo)

Local Ollama models are appended to fallback chains by default. Use `--no-local-fallbacks` to omit them.

Default preset: auto-detected from available API keys during `run_once_14-configure-opencode.sh.tmpl`. Detection order: both OpenAI + Anthropic keys → pro-plus-anthropic, Anthropic only → anthropic, OpenAI only → plus, no keys but Ollama → local, nothing → pro. Local-pro, local-mini, local-nano, and local-solo are manual-only (set via `DOTFILES_OPENCODE_TIER`).

### Model Classification Summary Table

| Role Category | Name Patterns | Required Capabilities | Fallback Priority |
|---------------|---------------------------------------------------------------|----------------------|-------------------|
| reasoning | `r1`, `reasoning`, `deep-think`, `think`, `qwq`, `reflection` | `thinking` + `tools` | oracle |
| code-gen | `coder`, `code`, `coding`, `devstral`, `codestral`, `laguna` | `thinking` + `completion` (name-qualified bypass) | orchestrator, fixer, designer |
| lightweight | `mini`, `small`, `tiny`, `phi`, `smol` | `tools` | librarian, explorer |
| vision | subset of lightweight with `vision` capability | `tools` + `vision` | observer |

---

## Fallback Chains

Each cloud tier defines fallback chains per agent role (orchestrator, oracle, librarian, explorer, fixer, designer). The `anthropic` and all `local-*` tiers have **empty fallback chains by default** — they rely on their single-provider model hierarchy instead. The `plus-anthropic` tier has mixed OpenAI + Anthropic fallback chains.

Local Ollama models are appended to fallback chains by default (unless `--no-local-fallbacks` is passed). Discovered local models are appended **per-role** (not uniformly): oracle gets reasoning models, orchestrator/fixer/designer get code-gen models, librarian/explorer get lightweight models, observer gets vision-capable models. All indexed models matching a role's category are appended (not just the single best model).

---

## Local Ollama Fallback Policy

Local Ollama models are appended to fallback chains by default. Use `--no-local-fallbacks` to omit them.

### Fallback Preset Selection (`--local-fallback-preset`)

By default, non-local tiers use the `local` tier's placeholder definitions to determine which local model categories to append to fallback chains. Use `--local-fallback-preset` to specify a different tier whose placeholders drive fallback selection:

```bash
# Use local-pro placeholders for richer fallback diversity
scripts/configure-opencode-tier.py --local-fallback-preset local-pro pro-plus

# Use local-mini placeholders (fewer categories) for lighter fallbacks
scripts/configure-opencode-tier.py --local-fallback-preset local-mini pro
```

For local tiers, `--local-fallback-preset` defaults to the current tier (so `local-pro` uses its own placeholders). For non-local tiers, it defaults to `local`.

### Placeholder Overrides (`--local-fallback-placeholder`)

Use `--local-fallback-placeholder` to override which model fills a specific placeholder category slot, without changing the entire preset. This is a category→model override applied before role-level overrides:

```bash
# Use a specific model for the vision placeholder
scripts/configure-opencode-tier.py --local-fallback-placeholder vision=ollama/qwen3.5:9b-mlx pro-plus

# Multiple overrides
scripts/configure-opencode-tier.py --local-fallback-placeholder vision=ollama/qwen3.5:9b-mlx --local-fallback-placeholder reasoning=ollama/qwq:32b pro-plus
```

Format: `--local-fallback-placeholder <category>=<model>` where the left side is one of `reasoning`, `code-gen`, `lightweight`, `vision` and the right side is a model name (e.g., `ollama/qwen3.5:9b-mlx`).

### Role Overrides (`--local-fallback-role`)

Use `--local-fallback-role` to override which specific model fills a specific agent role. This is a role-level override applied after placeholder overrides:

```bash
scripts/configure-opencode-tier.py --local-fallback-role observer=ollama/qwen3.5:9b-mlx pro-plus
```

Format: `--local-fallback-role <role>=<model>` where role is one of `orchestrator`, `oracle`, `librarian`, `explorer`, `fixer`, `designer`, `observer`.

### Override Order

Overrides are applied in this order:
1. **Discovery**: local Ollama models are discovered and classified
2. **Placeholder overrides** (`--local-fallback-placeholder`): remap which category fills each placeholder slot
3. **Role overrides** (`--local-fallback-role`): remap which model fills each role
4. **Fallback chain append**: all indexed models matching the (possibly overridden) placeholder keys are appended per role

### Multi-Model Fallback Appending

When local models are appended to fallback chains, all indexed variants matching the role's category are included — not just the single best model. For example, if both `reasoning` and `reasoning_2` placeholders are populated, both models appear in the oracle fallback chain.

### Environment Variable Forwarding

The chezmoi bootstrap script (`run_once_14-configure-opencode.sh.tmpl`) forwards these env vars to `configure-opencode.py`:
- `DOTFILES_LOCAL_FALLBACK_PRESET` → `--local-fallback-preset`
- `DOTFILES_LOCAL_FALLBACK_PLACEHOLDERS` → comma-separated `--local-fallback-placeholder` args (e.g. `reasoning=code-gen,vision=lightweight`)
- `DOTFILES_LOCAL_FALLBACK_ROLES` → comma-separated `--local-fallback-role` args (e.g. `observer=ollama/qwen3.5:9b-mlx`)

---

## Ollama Cloud Models

Ollama Cloud presets use models like `glm-5.2`, `glm-5.1`, `kimi-k2.6`, `kimi-k2.7-code`, `deepseek-v4-pro`, `deepseek-v4-flash` — the exact set varies by tier and is defined in `oh-my-opencode-slim.json`. Ollama Cloud Pro accounts have a 3-slot concurrency limit (3 concurrent requests per account, regardless of how many distinct models are used). Model lists are not hardcoded in mozart-router config — the GenericOpenAIAdapter auto-discovers available models from each gateway's `/v1/models` endpoint.

---

## Variant Policy

Variants control reasoning effort per agent role. They are set in `oh-my-opencode-slim.json` and passed through to the model provider. Valid variants: `low`, `medium`, `high`, `max`, `xhigh` (and no variant = model default).

**Role → variant conventions:**

| Role | Variant | Rationale |
|------|---------|-----------|
| orchestrator | none (default) | Coordination, doesn't need boosted reasoning |
| oracle | `max` or `xhigh` | Strategic advisor, needs deepest reasoning |
| council | same as oracle | Configured as a preset agent; drives multi-model consensus |
| librarian | `low` | Lookup/search, lightweight |
| explorer | `low` | Pattern matching, lightweight |
| designer | `medium` | Needs balance of creativity and precision |
| fixer | `high` (code-specialized) or `low` (general) | Execution focused |
| observer | `low` or none | Visual extraction, lightweight |

**Model-specific variant notes:**

| Model | Default behavior | Oracle variant | Notes |
|-------|-----------------|----------------|-------|
| `nemotron-3-ultra` | standard | `max` | MoE frontier reasoning; oracle/council use max variant |
| `minimax-m3` | standard | `low` | Vision+reasoning; last-resort fallback for observer |
| `claude-opus-4-8` | `high` | `xhigh` | Opus defaults to high reasoning; oracle needs xhigh to push deeper |
| `claude-opus-4-6` | standard | — | Used for orchestrator (anthropic, pro-plus-anthropic) and council gamma (anthropic); no variant needed |
| `claude-sonnet-4-6` | standard | `high` | Sonnet for designer/fixer roles; variant `low` for fixer, `medium` for designer |
| `deepseek-v4-pro` | standard | `max` | Upstream opencode-go uses max for oracle |
| `gpt-5.5` | standard | `high` | Upstream openai preset uses high for oracle |
| `deepseek-v4-flash` | standard | `high` | Upstream uses high for fixer (code execution) |
| `glm-5.1` | standard | none | Upstream uses no variant for orchestrator |
| `glm-5.2` | standard | max | 1M context; supports High/Max thinking effort; orchestrator uses max, oracle fallback uses max, other fallbacks use standard |
| `kimi-k2.6` | standard | none | Upstream uses no variant for observer, `medium` for designer |
| `kimi-k2.7-code` | standard | none | Code-focused; mandatory thinking (cannot disable); ~30% lower thinking tokens vs kimi-k2.6 |
| `gpt-5.4-mini` | standard | `high` | Upstream uses high for fixer (code execution) |
