# Junie Model Groups ↔ Oh My OpenCode Sync

> Deep reference for Junie model profile definitions and their alignment with OpenCode presets.

`configs/junie/model-groups.json` defines Junie model profiles that should stay aligned with `configs/opencode/oh-my-opencode-slim.json` presets. When changing one, update the other.

---

## Mapping Rule

| Junie field | oh-my-opencode-slim source | Notes |
|-------------|---------------------------|-------|
| `primaryModel` | `orchestrator` model | Strip provider prefix (e.g., `ollama-cloud/glm-5.2` → `glm-5.2`) |
| `fasterModel` | `librarian` model | Strip provider prefix; add `fasterProvider` if different from `provider` |
| `temperature` | Per-provider defaults | `ollama-cloud`: 0.7, `openai`: 1, `meridian`: 1, `ollama-local`: 0.6 |
| `modelTemperatures` | — (Junie-specific) | Model-family temperature map; applied per-role at profile generation time |

---

## Cross-Provider fasterModel

When the librarian model uses a different provider than the orchestrator, add a `fasterProvider` field to the group. The profile generator emits role-level `baseUrl`/`apiType`/`apiKey` overrides for the fasterModel:

```json
"pro-plus": {
  "provider": "ollama-cloud",
  "primaryModel": "glm-5.2",
  "fasterModel": "gpt-5.6-luna",
  "fasterProvider": "openai"
}
```

---

## Local Tier Placeholders

Local groups use `_local:<category>` placeholders (not hardcoded model names). These are resolved at profile generation time by `scripts/generate-jetbrains-profiles.py`, which imports `resolve_roles_from_list()` from `scripts/configure-opencode-tier.py`:

| Placeholder | Resolves to | Junie usage |
|-------------|-------------|-------------|
| `_local:reasoning` | Best local reasoning model | `local-pro` primaryModel |
| `_local:code-gen` | Best local code-gen model | `local`/`local-mini`/`local-nano` primaryModel |
| `_local:lightweight` | Best local lightweight model | `local-pro`/`local` fasterModel |
| `_local:vision` | Best vision-capable lightweight model | `local-mini` fasterModel |
| `_local:solo` | Best local solo model (all 4 caps) | `local-solo` primaryModel and fasterModel |

If a placeholder cannot be resolved (no local models in that category), the profile generator skips the group with a warning.

---

## Model Family Temperature Overrides

Junie recommends model-family-specific temperatures for optimal results. The `modelTemperatures` field in `model-groups.json` maps model name prefixes to recommended temperatures, applied via per-role `primaryModel.temperature` and `fasterModel.temperature` at profile generation time:

| Prefix | Temperature | Model families |
|--------|------------:|----------------|
| `anthropic` | 1 | Anthropic Claude |
| `claude` | 1 | Claude (alias) |
| `deepseek` | 0 | DeepSeek V3/V4 |
| `gemini` | 1 | Gemini |
| `gemma` | 1 | Gemma 2/3/4 |
| `glm` | 0.7 | GLM-4/5 |
| `gpt` | 1 | GPT |
| `kimi` | 0.8 | Kimi K2/K3 |
| `mimo` | 0.3 | MiMo |
| `qwen` | 0.6 | Qwen 2.5/3/3.5/3.6 |

Prefix matching is case-insensitive and uses longest-prefix-wins. If no prefix matches, the fallback temperature is 0.7.

All generated profiles emit temperatures exclusively via per-role `primaryModel.temperature` and `fasterModel.temperature` — never a top-level `temperature` field. This ensures each model's temperature is self-documenting and independent.

---

## Deployment

After changing `model-groups.json`:

```bash
python3 scripts/configure-jetbrains-ai.py --models
```

This generates profiles in `~/.junie/models/` and cleans up stale files.
