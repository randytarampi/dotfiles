# Junie Model Groups ↔ Oh My OpenCode Sync

> Deep reference for Junie model profile definitions and their alignment with OpenCode presets.

The shared tier registry in `scripts/lib/tier_registry.py` is the single source of truth for tier → role → model mapping. OpenCode, Junie, and Pi consume the presets in `configs/opencode/oh-my-opencode-slim.json` through this registry. `configs/junie/model-groups.json` supplies Junie-specific profile metadata, provider endpoints (including Google, OpenRouter, and OpenCode Zen), and temperature overrides.

### GitHub Copilot provider (experimental)

The `github-copilot` provider and `copilot` group are experimental and require
`GITHUB_TOKEN`. They use GitHub Copilot's OpenAI Responses-compatible endpoint;
validate the model ID and Junie compatibility before relying on the generated
profile. The generator skips this group when the token is unavailable.

## Other CLI provider capabilities

| Tool | Profile/provider config | Supported setup |
|------|--------------------------|------------------|
| Claude Code | No custom provider block in the deployed settings file | Use the upstream `ANTHROPIC_BASE_URL` and `ANTHROPIC_API_KEY` environment variables (Meridian uses its SDK/OAuth flow). |
| Gemini CLI | No custom provider block in the deployed settings file | Use the upstream `GOOGLE_GEMINI_BASE_URL` environment variable where supported; no dotfiles profile is generated. |
| GitHub Copilot CLI | No `~/.copilot/config.toml` provider/profile configuration | Provider selection is unsupported here; use Copilot CLI authentication and its command-line behavior. |

---

## Mapping Rule

| Junie field | oh-my-opencode-slim source | Notes |
|-------------|---------------------------|-------|
| `primaryModel` | Shared registry's `orchestrator` role | Strip provider prefix (e.g., `ollama-cloud/glm-5.2` → `glm-5.2`) |
| `fasterModel` | Shared registry's `librarian` role | Strip provider prefix; add `fasterProvider` if different from `provider` |
| `temperature` | Per-provider defaults | `ollama-cloud`: 0.7, `openai`: 1, `meridian`: 1, `ollama`: 0.6 |
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

Local groups use `_local:<category>` placeholders (not hardcoded model names). These are resolved at profile generation time by `scripts/generate-jetbrains-profiles.py` through `scripts/lib/tier_registry.py`:

| Placeholder | Resolves to | Junie usage |
|-------------|-------------|-------------|
| `_local:reasoning` | Best local reasoning model | `local-pro` primaryModel |
| `_local:code-gen` | Best local code-gen model | `local`/`local-mini`/`local-nano` primaryModel |
| `_local:lightweight` | Best local lightweight model | `local-pro`/`local` fasterModel |
| `_local:vision` | Best vision-capable lightweight model | `local-mini` fasterModel |
| `_local:solo` | Best local solo model (all 4 caps) | `local-solo` primaryModel and fasterModel |

If a placeholder cannot be resolved (no local models in that category), the profile generator skips the group with a warning.

For local tiers, when the selected code-gen model is a mixture-of-experts (MoE) model, it is also reused for the lightweight and vision categories. This keeps Junie groups usable on local installations where one MoE model supplies multiple capabilities.

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

After changing `model-groups.json` or the shared tier registry:

```bash
python3 scripts/configure-jetbrains-ai.py
```

This generates profiles in `~/.junie/models/` and cleans up stale files.

`configure-jetbrains-ai.py` accepts `--local-fallback-preset`, repeated
`--local-fallback-role`, repeated `--local-fallback-placeholder`, and
`--min-reasoning-embedding`. Use `--skip models` or `--skip dirs` to omit an
individual step. JetBrains configuration does not manage MCPs; MCP generation
belongs exclusively to the `mcps` step (`scripts/configure-mcps.py` or
`--skip mcps` on the umbrella orchestrator).
