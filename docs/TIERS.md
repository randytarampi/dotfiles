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

Fourteen tiers defined in `configs/opencode/oh-my-opencode-slim.json` (source of truth), consumed via `scripts/lib/tier_registry.py`:

| Tier | Providers | Best For |
|------|-----------|----------|
| **pro** | Ollama Cloud (glm-5.3-flash orchestrator, glm-5.3 council) | Daily coding, budget mode |
| **pro-plus** | Ollama Cloud + OpenAI (`gpt-5.6-terra`) | General development |
| **pro-plus-anthropic** | Anthropic + Ollama Cloud + OpenAI | Heavy orchestration |
| **plus** | OpenAI only (`gpt-5.6-terra`, `gpt-5.6-sol`, `gpt-5.6-luna`) | OpenAI-first workflow |
| **plus-anthropic** | OpenAI + Anthropic (no Ollama Cloud) | OpenAI + Anthropic hybrid |
| **anthropic** | Anthropic only | Anthropic-first workflow |
| **openai** | OpenAI (fallbacks: OpenCode Zen) | OpenAI-first workflow |
| **thirtydollars** | OpenAI + GitHub Copilot (fallbacks: OpenCode Zen) | Low-cost OpenAI workflow with Copilot designer |
| **opencode-zen-free** | OpenCode Zen (fallbacks: OpenAI) | Free OpenCode-hosted workflow — churn-prone, see [Catalog Churn Management](#catalog-churn-management) |
| **local-pro** | Local Ollama (all 4 categories: reasoning, code-gen, lightweight, vision) | Power users with diverse local models |
| **local** | Local Ollama (reasoning + code-gen + lightweight + vision) | Balanced offline/air-gapped |
| **local-mini** | Local Ollama (code-gen + lightweight + vision) | Minimal model diversity |
| **local-nano** | Local Ollama (single code-gen model + vision) | Single-model systems |
| **local-solo** | Local Ollama (single omnicapable model) | Maximum per-request quality, single-model simplicity |

> [!NOTE]
> When both `OLLAMA_API_KEY` and `ANTHROPIC_API_KEY` are set (but not `OPENAI_API_KEY`), auto-detection returns `pro-plus-anthropic`. The tier name implies OpenAI is also present, but the preset works correctly without it — Ollama Cloud handles orchestrator and Anthropic handles oracle.

Cloud presets (pro, pro-plus, pro-plus-anthropic) use Ollama Cloud models including `nemotron-3-ultra`, `minimax-m3`, `glm-5.3-flash`, `glm-5.3`, `glm-5.2`, `kimi-k3`, `kimi-k2.6` (legacy/degraded fallback), `deepseek-v4-flash`, and `gemma4:31b`. The `plus` preset uses OpenAI models exclusively. The `plus-anthropic` preset uses OpenAI and Anthropic models without Ollama Cloud. The `anthropic` preset uses only Anthropic models. The `local-pro` preset uses all four `_local:<category>` placeholders resolved at runtime. The `local` preset uses reasoning + code-gen + lightweight + vision for a balanced 3-party council. The `local-mini` preset reduces to code-gen + lightweight + vision. The `local-nano` preset uses a single code-gen model for all roles (except vision) with a 2+1 council. The `local-solo` preset uses a single omnicapable model (completion+thinking+tools+vision) for all roles, with council diversity from variants rather than different models.

The `openai`, `thirtydollars`, and `opencode-zen-free` presets omit observer
because their orchestrators are vision-capable. Zen free uses the multimodal
`muse-spark-1.2-contributor-free` orchestrator.

### OpenAI Tier (`openai`)

OpenAI-only preset adopted from [upstream oh-my-opencode-slim](https://github.com/alvinunreal/oh-my-opencode-slim/blob/master/docs/openai-preset.md), with local deviations (cross-provider fallbacks, observer removal):

| Role | Model | Variant |
|------|-------|---------|
| orchestrator | `gpt-5.6-terra` | high |
| oracle | `gpt-5.6-sol` | high |
| librarian | `gpt-5.6-luna` | low |
| explorer | `gpt-5.6-luna` | low |
| designer | `gpt-5.6-luna` | medium |
| fixer | `gpt-5.6-luna` | high |
| council | `gpt-5.6-sol` | high (α sol, β terra, γ luna) |

Fallbacks cross to OpenCode Zen (terra→Zen terra for orchestrator, big-pickle for oracle, nemotron-3.5-lightning-free elsewhere) so the preset still runs when OpenAI quota is exhausted. Observer omitted — terra is vision-capable.

### Thirtydollars Tier (`thirtydollars`)

Same OpenAI anchors as `openai`, with the Copilot Gemini designer — adopted from [upstream oh-my-opencode-slim](https://github.com/alvinunreal/oh-my-opencode-slim/blob/master/docs/thirty-dollars-preset.md), with local deviations (cross-provider fallbacks, observer removal):

| Role | Model | Variant |
|------|-------|---------|
| orchestrator | `openai/gpt-5.6-terra` | medium |
| oracle | `openai/gpt-5.6-sol` | high |
| librarian / explorer | `openai/gpt-5.6-luna` | low |
| designer | `github-copilot/gemini-3.5-flash` | — |
| fixer | `openai/gpt-5.6-luna` | medium |
| council | `openai/gpt-5.6-sol` | high (α sol, β terra, γ luna) |

Designer fallback: `opencode/gemini-3.5-flash` (same model via Zen, dodging Copilot quota). Observer omitted — terra is vision-capable. Requires GitHub Copilot auth via `/connect`.

### OpenCode Zen Free Tier (`opencode-zen-free`)

Zero-cost preset on OpenCode Zen's free catalog — adopted from [upstream oh-my-opencode-slim](https://github.com/alvinunreal/oh-my-opencode-slim/blob/master/docs/opencode-zen-free-preset.md), with local deviations (orchestrator re-anchored to `muse-spark-1.2-contributor-free` after upstream's `x-preview-f-free` left the catalog; observer removed — the upstream-documented orchestrator is gone and ours is vision-capable). Requires `OPENCODE_API_KEY` (or `/connect`):

| Role | Model | Variant / Temp |
|------|-------|----------------|
| orchestrator | `muse-spark-1.2-contributor-free` | high, temp 0.4 |
| oracle | `big-pickle` | max, temp 0.4 |
| librarian / explorer / fixer | `nemotron-3.5-lightning-free` | low — / high (fixer), temp 0.2 |
| designer | `mimo-v2.5-free` | medium, temp 0.3 |
| council | `big-pickle` | max (α big-pickle, β nemotron-3.5-lightning-free, γ mimo-v2.5-free) |

Fallbacks point at OpenAI paid models (terra/luna) — free-tier failures degrade to paid capacity. Observer omitted — the Muse orchestrator accepts image/video/pdf/audio input. `muse-spark-1.2-contributor-free` is the Meta **contributor tier**: free because Meta may train on prompts and completions sent through it — avoid pointing confidential work at this preset (documented in [docs/OPENCODE.md](OPENCODE.md)).

### Pro Tier (`pro`)

Ollama Cloud budget preset using the approved Anthropic-to-Ollama Cloud cost-tier alignment:

| Role | Model | Variant |
|------|-------|---------|
| orchestrator | `glm-5.3-flash` | max |
| oracle | `kimi-k3` | max |
| librarian | `gemma4:31b` | low |
| explorer | `gemma4:31b` | low |
| designer | `glm-5.3-flash` | medium |
| fixer | `deepseek-v4-flash` | high |
| council | `glm-5.3` | max |

Fallbacks are provider-deduplicated and retain one best alternative per role.

### Anthropic Tier (`anthropic`)

Anthropic-only preset with no OpenAI or Ollama Cloud providers:

| Role | Model | Variant |
|------|-------|---------|
| orchestrator | `claude-sonnet-5` | — |
| oracle | `claude-fable-5` | xhigh |
| librarian | `claude-haiku-4-5` | low |
| explorer | `claude-haiku-4-5` | low |
| designer | `claude-sonnet-5` | medium |
| fixer | `claude-haiku-4-5` | high |
| observer | `claude-haiku-4-5` | low |

Council agent is defined inside each preset's agent list; alpha `claude-fable-5`, beta `claude-sonnet-5`, gamma `claude-opus-5`. Council synthesizer uses `claude-opus-5` with xhigh variant. Orchestrator now uses `claude-sonnet-5` (swapped from opus-5 for cost optimization). Empty fallback chains by default — local Ollama models are appended automatically unless `--no-local-fallbacks` is passed.

### Plus-Anthropic Tier (`plus-anthropic`)

OpenAI + Anthropic preset with no Ollama Cloud providers:

| Role | Model | Variant |
|------|-------|---------|
| orchestrator | `openai/gpt-5.6-terra` | — |
| oracle | `anthropic/claude-fable-5` | xhigh |
| librarian | `openai/gpt-5.6-luna` | low |
| explorer | `anthropic/claude-haiku-4-5` | low |
| designer | `anthropic/claude-sonnet-5` | medium |
| fixer | `anthropic/claude-haiku-4-5` | high |
| observer | `anthropic/claude-haiku-4-5` | low |

Council agent is defined inside each preset's agent list; alpha `claude-fable-5`, beta `gpt-5.6-terra`, gamma `gpt-5.6-luna`. Council synthesizer uses `claude-opus-5` with xhigh variant. Fallback chains mix OpenAI + Anthropic models per role — local Ollama models are appended automatically unless `--no-local-fallbacks` is passed.

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

### MoE Code-Gen Reuse Across Tools

For every `local*` tier, OpenCode (via `configure-opencode-tier.py`), Junie
(via `generate-jetbrains-profiles.py`), and Pi (via `configure-pi.py`) pass
`moe_codegen_reuse=True` to `resolve_roles_from_list`. When the selected
code-gen model is MoE and vision-capable (for example,
`ornith-1.5:35b`), one model serves code-gen, lightweight, and vision roles.
Thus librarian, explorer, and observer — along with Pi's researcher, scout,
and reviewer built-ins — use the same MoE model.

In Junie's local groups, `fasterModel: _local:lightweight` consequently
resolves to the code-gen model under reuse, so primary and faster model can be
the same. This is intentional: it minimizes the number of loaded models.

The optional `--min-reasoning-embedding N` filter is forwarded consistently to
OpenCode, Junie, and Pi, excluding reasoning models with thinner embeddings
than `N`. It can also be set with `DOTFILES_MIN_REASONING_EMBEDDING`.

---

## Local Model Classification

Placeholders are resolved by `scripts/lib/tier_registry.py` (consuming `oh-my-opencode-slim.json` presets) using model name heuristics, size rules, `ollama show` parameter counts, capability-aware classification, and density-aware sorting:
- **reasoning**: models containing `r1`, `reasoning`, `deep-think`, `think`, `qwq`, `reflection`
- **code-gen**: models containing `coder`, `code`, `coding`, `devstral`, `codestral`, `deepseek-coder`, `qwen2.5-coder`, `qwen3-coder`, `codeqwen`, `ornith`
- **lightweight**: models containing `mini`, `small`, `tiny`, `phi`, `gemma:2`, `gemma3`, `smol`
- **vision**: subset of `lightweight` models that also have the `vision` capability (from `ollama show`)
- **audio**: subset of `lightweight` models that also have the `audio` capability (from `ollama show`)
- **solo**: models with all four capabilities (`completion` + `thinking` + `tools` + `vision`), purely capability-based (no name heuristics), sorted by density-aware key (dense > MoE, then embedding_length, then param count)

Indexed placeholders (`_local:<category>_2`) resolve to the second-best model in a category, ensuring council diversity. For example, `_local:code-gen_2` gives a different model from `_local:code-gen` when multiple code-gen models are available, or falls back to the second-best reasoning model if code-gen only has one entry.

Additional classification rules (applied after name heuristics):
- **Size rule**: models with `ollama list` SIZE < 12 GB are classified as `lightweight`
- **`ollama show` parameter-based**: unclassified models (≥ 12 GB, no name heuristic match) are classified via `ollama show` parameter count — parameters ≥ 7B → reasoning, parameters < 7B → code-gen (not lightweight)
- **Capability filtering**: after initial classification, each category is filtered by required capabilities parsed from `ollama show`:
  - `reasoning` requires `thinking` + `tools`
  - `code-gen`: name-heuristic-qualified models bypass capability checks; models classified via size/fallback rules require `thinking` + `completion`
  - `lightweight` requires `tools`
  - `vision` requires `tools` + `vision` (subset of lightweight)
  - `audio` requires `tools` + `audio` (subset of lightweight)
  - `solo` requires `completion` + `thinking` + `tools` + `vision` (no name heuristics)
- **Cross-category promotion**: models name-heuristically routed to `code-gen` but with `thinking` + `tools` capabilities are also added to the `reasoning` bucket. This lets dense coding/general models (e.g. `qwen3.6:27b-coding-nvfp4`) serve as `reasoning_2`/`reasoning_3` when their embedding and density justify it, without losing their primary category assignment. Lightweight models are **not** promoted — they are small by definition and should not serve as reasoning models when larger models are available.
- **Density-aware sorting** (reasoning + solo only): after capability filtering, models are sorted by `(non_lightweight_first, dense_first, embedding_length, param_count)` descending. Lightweight-origin models sort last so a 9B lightweight model never outranks a 35B for deep reasoning. Among non-lightweight models, dense (non-MoE) models rank above MoE models of equal or larger parameter count, and higher embedding dimensions rank above lower. This prevents thin-embedding MoE models from outranking denser models for deep reasoning. `code-gen` and `lightweight` keep the param-count sort (MoE breadth helps code-gen diversity).
- **Embedding hard filter** (optional): `--min-reasoning-embedding N` (or `DOTFILES_MIN_REASONING_EMBEDDING=N` env var) excludes models with `embedding_length < N` from reasoning roles entirely. Default `0` = disabled. Models with unknown embedding_length are never filtered out. Useful to hard-exclude MoE models with very thin embeddings (e.g. `--min-reasoning-embedding 3200`).
- **Code-gen reuse**: if no code-gen model is found via name heuristic, the reasoning model is reused for code-gen roles
- **MoE code-gen reuse**: when `moe_codegen_reuse` is enabled for local* presets and the chosen code-gen model is MoE, it also serves librarian, explorer, and (when vision-capable) observer roles to minimize loaded models
- **Vision fallback**: if no vision-capable model exists, the best lightweight model is used with a warning
- **Indexed placeholders**: `_local:<category>_2` resolves to the second-best model in a category (e.g., `_local:code-gen_2` for council gamma diversity)

**Runtime warnings**: `configure-opencode-tier.py` warns when council councillors resolve to the same model (limited diversity), and reports total distinct models available across categories. These warnings are generated from the shared tier registry via `scripts/lib/tier_registry.py`.

Switch tier: `scripts/configure-opencode-tier.py --preset <tier>` (pro, pro-plus, pro-plus-anthropic, plus, plus-anthropic, anthropic, openai, thirtydollars, opencode-zen-free, local-pro, local, local-mini, local-nano, local-solo)

Local Ollama models are appended to fallback chains by default. Use `--no-local-fallbacks` to omit them.

Default preset: auto-detected from available API keys during OpenCode configuration. Detection order: both OpenAI + Anthropic keys → pro-plus-anthropic, Anthropic only → anthropic, OpenAI only → plus, no keys but Ollama → local, nothing → pro. Local-pro, local-mini, local-nano, and local-solo are manual-only (set via `DOTFILES_OPENCODE_TIER`).

### Model Classification Summary Table

| Role Category | Name Patterns | Required Capabilities | Fallback Priority |
|---------------|---------------------------------------------------------------|----------------------|-------------------|
| reasoning | `r1`, `reasoning`, `deep-think`, `think`, `qwq`, `reflection` | `thinking` + `tools` | oracle |
| code-gen | `coder`, `code`, `coding`, `devstral`, `codestral`, `laguna`, `ornith` | `thinking` + `completion` (name-qualified bypass) | orchestrator, fixer, designer |
| lightweight | `mini`, `small`, `tiny`, `phi`, `smol` | `tools` | librarian, explorer |
| vision | subset of lightweight with `vision` capability | `tools` + `vision` | observer |
| audio | subset of lightweight with `audio` capability | `tools` + `audio` | — |

> [!TIP]
> `reasoning` and `solo` use density-aware sorting: dense (non-MoE) models outrank MoE models, and higher `embedding_length` wins ties. Use `--min-reasoning-embedding N` to hard-exclude thin-embedding MoE models. `code-gen` and `lightweight` keep the param-count sort.

---

## Fallback Chains

Each cloud tier defines fallback chains per agent role (orchestrator, oracle, librarian, explorer, fixer, designer). The `anthropic` tier has orchestrator and oracle fallback entries; all `local-*` tiers have **empty fallback chains by default** and rely on their single-provider model hierarchy. The `plus-anthropic` tier has mixed OpenAI + Anthropic fallback chains. The `openai`/`thirtydollars` tiers fall back to OpenCode Zen, and `opencode-zen-free` falls back to OpenAI paid models — cross-provider fallbacks by design, one entry per provider per role (invariant 7 in [scripts/verify-slim-invariants.py](../scripts/verify-slim-invariants.py)).

Local Ollama models are appended to fallback chains by default (unless `--no-local-fallbacks` is passed). Discovered local models are appended **per-role** (not uniformly): oracle gets reasoning models, orchestrator/fixer/designer get code-gen models, librarian/explorer get lightweight models, observer gets vision-capable models. All indexed models matching a role's category are appended (not just the single best model).

---

## Local Ollama Fallback Policy

Local Ollama models are appended to fallback chains by default. Use `--no-local-fallbacks` to omit them.

### Fallback Preset Selection (`--local-fallback-preset`)

By default, non-local tiers use the `local` tier's placeholder definitions to determine which local model categories to append to fallback chains. Use `--local-fallback-preset` to specify a different tier whose placeholders drive fallback selection:

```bash
# Use local-pro placeholders for richer fallback diversity
scripts/configure-opencode-tier.py --local-fallback-preset local-pro --preset pro-plus

# Use local-mini placeholders (fewer categories) for lighter fallbacks
scripts/configure-opencode-tier.py --local-fallback-preset local-mini --preset pro
```

For local tiers, `--local-fallback-preset` defaults to the current tier (so `local-pro` uses its own placeholders). For non-local tiers, it defaults to `local`.

### Placeholder Overrides (`--local-fallback-placeholder`)

Use `--local-fallback-placeholder` to override which model fills a specific placeholder category slot, without changing the entire preset. This is a category→model override applied before role-level overrides:

```bash
# Use a specific model for the vision placeholder
scripts/configure-opencode-tier.py --local-fallback-placeholder vision=ollama/qwen3.5:9b-mlx --preset pro-plus

# Multiple overrides
scripts/configure-opencode-tier.py --local-fallback-placeholder vision=ollama/qwen3.5:9b-mlx --local-fallback-placeholder reasoning=ollama/qwq:32b --preset pro-plus
```

Format: `--local-fallback-placeholder <category>=<model>` where the left side is one of `reasoning`, `code-gen`, `lightweight`, `vision` and the right side is a model name (e.g., `ollama/qwen3.5:9b-mlx`).

### Role Overrides (`--local-fallback-role`)

Use `--local-fallback-role` to override which specific model fills a specific agent role. This is a role-level override applied after placeholder overrides:

```bash
scripts/configure-opencode-tier.py --local-fallback-role observer=ollama/qwen3.5:9b-mlx --preset pro-plus
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

The OpenCode configure script forwards these env vars to `configure-opencode.py`:
- `DOTFILES_LOCAL_FALLBACK_PRESET` → `--local-fallback-preset`
- `DOTFILES_LOCAL_FALLBACK_PLACEHOLDERS` → comma-separated `--local-fallback-placeholder` args (e.g. `reasoning=code-gen,vision=lightweight`)
- `DOTFILES_LOCAL_FALLBACK_ROLES` → comma-separated `--local-fallback-role` args (e.g. `observer=ollama/qwen3.5:9b-mlx`)

---

## Project Presets (Orthogonal to Global Tier)

`configure-project.py --preset <tier>` writes a **self-sufficient** project `opencode.json`. It derives the set of providers the preset references (via `get_preset_providers()` in `scripts/lib/opencode_config.py`) and emits a `provider` block for each — `openai`, `anthropic`, `ollama-cloud`, `ollama`, `opencode`, and/or `github-copilot` — rather than relying on the global `~/.config/opencode/opencode.json` to define them.

This makes project presets **orthogonal** to the global tier: a project using `--preset anthropic` works whether the global tier is `pro-plus-anthropic` (a superset) or `pro` (which globally disables Anthropic). The project config also resets `disabled_providers: []` so an unrelated global tier's `disabled_providers` cannot suppress the project's providers.

> [!IMPORTANT]
> If you run `configure-opencode-tier.py` alone in a project (skipping the `opencode` step), the project-root `opencode.json` is not refreshed and may be missing providers the preset references. Always run `configure-project.py` (default steps include `opencode`) when switching to a preset orthogonal to the global tier.

---

## Ollama Cloud Models

Ollama Cloud presets use models like `glm-5.3-flash`, `glm-5.3`, `kimi-k3`, `deepseek-v4-flash`, and `gemma4:31b` — the exact set varies by tier and is defined in `oh-my-opencode-slim.json`. Ollama Cloud Pro accounts have a 3-slot concurrency limit (3 concurrent requests per account, regardless of how many distinct models are used). Model lists are not hardcoded in mozart-router config — the GenericOpenAIAdapter auto-discovers available models from each gateway's `/v1/models` endpoint.

## OpenAI Models (gpt-5.6 Family)

The gpt-5.6 family replaces the gpt-5.5/gpt-5.4 family as the primary OpenAI model line:

| Model | Role | Description |
|-------|------|-------------|
| `gpt-5.6-terra` | Balanced | Primary orchestrator and general-purpose model. Replaces gpt-5.5 (balanced) as the default for orchestrator, librarian, and general roles. |
| `gpt-5.6-sol` | Flagship | Primary oracle and deep reasoning model. Replaces gpt-5.5 (flagship) for oracle, council, and complex analysis. |
| `gpt-5.6-luna` | Lightweight | Primary lightweight model for librarian, explorer, and fixer roles. Replaces gpt-5.4-mini and gpt-5.4-nano. |

**Fallback chain**: When gpt-5.6 models are unavailable, the tier falls back to gpt-5.5 (flagship) → gpt-5.4-mini → gpt-5.4-nano in degraded mode. The `plus` and `plus-anthropic` presets define these fallback chains per role in `oh-my-opencode-slim.json`.

## Catalog Churn Management

Model catalogs are not stable. Two churn classes have bitten this repo already:

- **Free-tier churn** (fast): OpenCode Zen removed `x-preview-f-free` and `hy3-free` from its free catalog between 2026-08 and 2026-09, mid-changeset. Free IDs are promotional and disappear without notice — treat every `-free` reference as having a short expected lifespan.
- **Paid-generation churn**: model families get replaced (gpt-5.5 → gpt-5.6, glm-5.2 → glm-5.3). Anchored tiers survive because the replacement follows the same role profile; the danger is referencing a removed ID directly.

### Detection

1. `make check-model-drift` — validates slim.json references against checked-in allowlists, probes live local Ollama and deployed Junie profile endpoints, and warns when the sync stamp is >14 days old (run as part of `make verify`; excluded from `ci-verify` because it needs live endpoints).
2. `opencode models <provider> --refresh` — the only authoritative view of a provider's *current* catalog (models.dev pages and OpenCode's local cache lag; both have shown stale IDs).
3. `make deploy` prints a staleness notice when the model-sync stamp is missing or old (written by `generate-jetbrains-profiles.py` on real runs).

### Replacement procedure (when a referenced ID disappears)

1. Confirm against the live catalog (`opencode models <provider> --refresh`); do not substitute from memory or docs.
2. Match the role's capability profile, not just the name tier: context window, output cap, vision input, tool/reasoning support. (e.g. `ling-3.0-flash-fin-free` looks like an orchestrator candidate but its 32K output cap and text-only input disqualify it; `muse-spark-1.2-contributor-free`'s multimodality is what allowed dropping the Zen observer.)
3. Update in lockstep: `oh-my-opencode-slim.json` (preset + fallbacks + council), the provider allowlist JSON, tier-registry tests, and the tier docs — the same 6-way sync rule as [docs/MODEL_UPDATES.md](MODEL_UPDATES.md).
4. Re-run: `make check-slim-invariants test-tier-registry check-model-drift` and a `configure-opencode.py` dry-run for the affected preset.
5. Record the churn event in [docs/MODEL_UPDATES.md](MODEL_UPDATES.md) (dated) — the removal history is the evidence base for judging how much trust a free tier deserves.

### Stability expectations by tier class

| Class | Tiers | Churn exposure | Policy |
|-------|-------|----------------|--------|
| Paid anchored | anthropic, openai, plus, plus-anthropic, pro-plus*, thirtydollars | Low — generations replace each other, IDs persist | Re-anchor only on announced deprecations |
| Ollama Cloud | pro, pro-plus (ollama-cloud roles) | Medium — library tags evolve (`:cloud` aliases, dated variants) | Prefer stable aliases over dated tags; `check-model-drift` catches removals |
| Free/promotional | opencode-zen-free | High — IDs vanish without notice | Expect periodic re-anchoring; cross-provider fallbacks (OpenAI paid) keep the tier usable when free IDs die; re-verify with `opencode models opencode --refresh` before relying on it |

**Design rules that keep churn survivable** (already enforced):

- Every preset's fallbacks cross to a *different* provider than its primaries (invariant 7 caps same-provider fallbacks at one), so a single provider's catalog change never leaves a role with zero alternatives.
- Free-tier roles degrade to paid capacity rather than breaking.
- `muse-spark-1.2-contributor-free` and other contributor tiers may train on prompts/completions upstream — documented in the tier section above and [docs/OPENCODE.md](OPENCODE.md); treat free-tier presets as non-confidential workflows.

## Council Synthesizers

The `council` key in each tier's `_tiers` block of `oh-my-opencode-slim.json` defines councillor presets and the council synthesizer model per tier. There is no separate council fallback key — synthesizer availability follows the provider:

- **pro**: synthesizer `glm-5.3` (max variant)
- **pro-plus**: synthesizer `gpt-5.6-sol` (high variant)
- **pro-plus-anthropic**: synthesizer `gpt-5.6-sol` (high variant)
- **plus**: synthesizer `gpt-5.6-sol` (high variant)
- **plus-anthropic**: synthesizer `claude-opus-5` (xhigh variant)
- **anthropic**: synthesizer `claude-opus-5` (xhigh variant)
- **openai / thirtydollars**: synthesizer `gpt-5.6-sol` (high variant; councillors α sol, β terra, γ luna)
- **opencode-zen-free**: synthesizer `big-pickle` (max variant; councillors α big-pickle, β nemotron-3.5-lightning-free, γ mimo-v2.5-free)

Councillors are defined per tier under `council.presets` in `oh-my-opencode-slim.json` and applied automatically by `configure-opencode-tier.py`.

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
| `claude-opus-5` | `high` | `xhigh` | Opus defaults to high reasoning; council gamma/synthesizer (xhigh), meridian-opus profile |
| `claude-opus-4-8` | `high` | `xhigh` | Legacy model retained as a degraded fallback |
| `claude-opus-4-6` | standard | — | Legacy model retained in registry only; no active Anthropic preset roles |
| `claude-sonnet-5` | standard | — | Used for orchestrator (no variant), designer (medium), council beta (no variant), plus-anthropic council fallback; no synth role (opus-5) |
| `claude-fable-5` | standard | `xhigh` | Used for oracle (xhigh, deep reasoning) and council alpha (no variant) |
| `claude-haiku-4-5` | standard | `low/high` | Librarian/explorer/observer use low; fixer uses high |
| `claude-sonnet-4-6` | standard | `high` | Legacy model used by meridian-sonnet profile; no active Anthropic preset roles |
| `deepseek-v4-pro` | standard | `max` | Catalog-only legacy model; no active tier role |
| `gpt-5.6-terra` | standard | `high` | Primary balanced model; orchestrator default, oracle uses high |
| `gpt-5.6-sol` | standard | `high` | Primary flagship model; oracle uses high, council uses high |
| `gpt-5.6-luna` | standard | `high` | Primary lightweight model; librarian/explorer use low, fixer uses high |
| `deepseek-v4-flash` | standard | `high` | Upstream uses high for fixer (code execution) |
| `glm-5.2` | standard | max | 1M context; supports High/Max thinking effort; orchestrator uses max, oracle fallback uses max, other fallbacks use standard |
| `glm-5.3-flash` | standard | max | 320B/18B MoE, 1M context, vision (images+video), always-on tunable thinking; orchestrator uses max (replaces glm-5.2 as primary orchestrator in pro/pro-plus); glm-5.2 remains as secondary fallback |
| `glm-5.3` | standard | max | 753B MoE flagship, 1M context, text-only; strictly dominates glm-5.2 on all 13 shared benchmarks; leads nemotron-3-ultra on GDPval-AA v2 (1769 vs 1448, NVIDIA vendor-reported); orchestrator/oracle fallbacks use max |
| `kimi-k3` | max | max | Reasoning effort defaults to max; council gamma uses max or no variant |
| `kimi-k2.6` | standard | none | Legacy model retained as a degraded fallback; upstream uses no variant for observer, `medium` for designer |
| `kimi-k2.7-code` | standard | none | Catalog-only legacy code-focused model; mandatory thinking (cannot disable) |
| `gpt-5.5` | standard | `high` | Legacy flagship; now a degraded fallback when gpt-5.6-sol is unavailable |
| `gpt-5.4-mini` | standard | `high` | Legacy lightweight; now a degraded fallback when gpt-5.6-luna is unavailable |
| `gpt-5.4-nano` | standard | `high` | Legacy nano; now a degraded fallback when gpt-5.6-luna is unavailable |
