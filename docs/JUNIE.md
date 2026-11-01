# Junie Model Groups ↔ Oh My OpenCode Sync


Groq, Mistral, Cloudflare Workers AI and NVIDIA NIM have opt-in direct
model groups and LiteLLM twins. Groups without credentials or advertised
proxy aliases are not guaranteed usable. See
[OPTIONAL_INFERENCE.md](OPTIONAL_INFERENCE.md) for configuration and tests.

> Deep reference for Junie model profile definitions and their alignment with OpenCode presets.

The shared tier registry in `scripts/lib/tier_registry.py` is the single source of truth for tier → role → model mapping. OpenCode, Junie, and Pi consume the presets in `configs/opencode/oh-my-opencode-slim.json` through this registry. `configs/junie/model-groups.json` supplies Junie-specific profile metadata, provider endpoints (including Google, OpenRouter, and OpenCode Zen), and temperature overrides.

The tracked group's `sharedPresets` entry derives `free` from the shared
orchestrator and librarian assignments before generating direct or gateway
profiles. An explicit `--groups-json` snapshot without that metadata retains
its own frozen assignments. Free classification follows model-name markers or
Cohere/Poolside when served directly or through this gateway; it is assignment
metadata, not request-time zero-cost qualification. Third-party hosting does
not inherit provider free status; see [the acceptance ledger](POST72-ACCEPTANCE.md).
Dynamic free presets are composed from fresh catalogue and route-evidence inputs
when profiles are generated. Missing, stale or insufficient provenance fails
closed rather than selecting a paid substitute. This is not a request-time cost
or entitlement guarantee, and no live catalogue status is claimed here.
An optional `requiredGroups` list in `model-groups.json` names groups that must
resolve: an unavailable listed dynamic-free group aborts before profile writes.
Unlisted dynamic-free groups are optional and omitted when their evidence is
unavailable; unrelated groups continue, with no stale or paid substitute.
Compatibility names `openai-gpt-6-luna`, `openai-gpt-6.1-sol` and the four
`meridian-*` names generate selectable profiles equivalent to their renamed
`openai-*` and `anthropic-*` groups, including derived gateway twins. Retain
these names until downstream usage is checked and a sunset is agreed.
Ollama Cloud's authenticated catalogue offers `deepseek-v4-pro:0813`, not
`deepseek-v4-pro`; the owner approved retaining the verified versioned ID.

Set `DOTFILES_USE_LITELLM_PROXY=1` to route Junie through the LiteLLM gateway.
This gateway-only switch requires `DOTFILES_RUN_LITELLM_SETUP=1`, Junie's client
key file and exact gateway aliases; missing prerequisites are errors, with no
direct-provider fallback. See [LiteLLM Proxy](LITELLM.md) for setup and
client-key requirements. The LiteLLM provider uses the app-specific
`LITELLM_JUNIE_KEY`. The tracked `configs/junie/model-groups.json` keeps only
the `apiKeyEnv` selector; no key is stored in tracked configuration. After
LiteLLM provisions the key in its generated `service.env`, configuration
derives `~/.local/share/litellm/clients/junie.key` with mode 600 in a mode-700
directory. The generator reads that file directly (not `service.env` or an
inherited environment variable) and places the Junie-only virtual key as a
literal in the generated private profile JSON under `~/.junie/models/` (the
shared `~/.ai/models/` directory). This is an intentional credential-bearing
runtime artifact: keep the profile directory private, do not copy/share those
profiles, and rotate via LiteLLM provisioning plus `make deploy`. With the
gateway enabled, tier and generated local-pool profiles for supported providers
are routed only through exact aliases present in Junie's authenticated LiteLLM
catalogue. Missing aliases omit the generated profile; an unavailable catalogue
preserves existing owned files and provenance without claiming readiness. Missing faster aliases omit
only `fasterModel`. No eligible profile silently falls back to a direct endpoint.
The generator derives a `litellm-<plain>` twin for every plain group routed
through `LITELLM_ROUTED_PROVIDERS`, including future `local-*` groups, using the
existing alias mapping and live-catalogue filter. A verification check rejects
routed groups that fail to produce a matching twin. Deployment remains direct by default;
routed profiles are emitted only under `DOTFILES_USE_LITELLM_PROXY=1`. This is
fail-closed: missing aliases or keys never trigger a direct substitute, and an
unavailable catalogue omits gateway groups instead of publishing unverified
aliases. The four `litellm-anthropic-*` twins shipped after a reproduced
end-to-end Junie gateway smoke test (2026-10-16). These four twins are derived
from the plain groups by the same generator contract as other routed providers;
their exact `meridian/claude-*` aliases remain subject to the authenticated live
catalogue filter.

Named `litellm-*` groups keep their existing catalogue-filter behaviour.
With the gate off, Meridian groups keep their native OpenAI Responses endpoint
and protocol at `/v1/responses`. With the gate on, the derived Meridian twins
use authenticated, catalogue-qualified `meridian/<model>` aliases through the
LiteLLM chat-completions endpoint. The Meridian gateway qualification and
protocol smoke evidence is recorded above; no direct Responses profile is
rewritten into a gateway profile. Copilot remains direct because it uses its own
authentication and profile shape. The gate defaults to `0`, and disabled mode
emits the original direct groups unchanged.

After enabling, disabling or rotating the Junie LiteLLM key, regenerate profiles
with `make deploy`. Restart Junie CLI sessions and reload/restart the IntelliJ
IDE so both consumers reopen the shared profiles and pick up the new key.

The GitHub Copilot provider metadata is retained for a future verified setup;
there is no active Junie Copilot model group until a usable model ID and profile
compatibility are confirmed.

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

Gateway twins are derived from routed plain groups by the profile generator;
the `litellm-` prefix does not change the source model-family temperature.
See the generator and invariant check for the mapping and no-silent-drop contract.

## Local profile family passthrough

Each local tier profile generated by `generate-jetbrains-profiles.py` uses its
corresponding `_tiers.<tier>.profile.name` (`quiet`, `balanced` or `loud`).
Cloud groups are unchanged. A literal
Junie group must provide `provider`, `primaryModel` and the discovered runtime
model ID in the shape required by the Junie schema; see the JetBrains model
profile schema reference before adding one.

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

Local groups use `_local:<category>` placeholders (not hardcoded model names). These are resolved at profile generation time by `scripts/generate-jetbrains-profiles.py` through `scripts/lib/tier_registry.py`. oMLX provider metadata is preserved through the wrapper's JSON model argument; oMLX groups use an OpenAI-compatible endpoint and `omlx/<model>` references.

Beyond the tier-driven groups, the generator appends one selectable profile per chat-capable pool model (`local-<provider>-<slug>.json`) from every gate-active engine in `scripts/lib/local_engines.py` — the N-engine contract: a newly registered engine's models become selectable in JetBrains AI with no consumer change. `fasterModel` chains to the same engine's next-ranked chat model when one exists.

| Placeholder | Resolves to | Junie usage |
|-------------|-------------|-------------|
| `_local:reasoning` | Best local reasoning model | `local-quiet`, `local` and `local-loud` primaryModel |
| `_local:code-gen` | Best local code-gen model | `local-quiet`, `local` and `local-loud` primaryModel |
| `_local:lightweight` | Best local lightweight model | `local-quiet`, `local` and `local-loud` fasterModel |
| `_local:vision` | Best vision-capable lightweight model | `local-quiet`, `local` and `local-loud` fasterModel |
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
Generated files are atomically written with mode 600 and the shared models
directory is kept at mode 700. Cleanup is limited to profiles recorded as
generator-owned; unrelated user JSON profiles are preserved. If the legacy
`.dotfiles-generated-profiles.json` file remains inside the models directory,
generation refuses before changing profiles and reports its exact path. Back it
up privately outside the models directory, review its contents, and relocate it
manually to the canonical manifest location only if that location is absent;
otherwise stop and reconcile the two privately before retrying. The
generator never deletes, moves or adopts the legacy file or any names it lists.
Dry-run performs the same ownership and collision checks and reports this
refusal without writing.

The canonical digest manifest is
`~/.local/share/dotfiles/jetbrains-profiles-manifest.json`; the legacy manifest
location is `~/.junie/models/.dotfiles-generated-profiles.json`. If generation
is interrupted after replacing a profile but before publishing the updated
canonical manifest, the next run refuses that profile because its bytes no
longer match the recorded ownership digest. This is intentional manual recovery,
not automatic adoption or a journalled transaction. Privately back up the
affected profile and both manifest locations before investigating. Compare the
profile's SHA-256 with the exact expected digest in the canonical manifest and
audit its contents and provenance; stop if either manifest, the expected hash
or the ownership history is uncertain. Only the owner should repair the audited
generated artifact/ledger, or test regeneration in a disposable directory before
deliberately repairing the ledger. Never adopt a profile automatically, and
never delete or rewrite unrelated manual profiles during recovery.

`configure-jetbrains-ai.py` accepts `--local-fallback-preset`, repeated
`--role-model`, repeated `--category-model`, and
`--min-reasoning-embedding`. Use `--skip models` or `--skip dirs` to omit an
individual step. JetBrains configuration does not manage MCPs; MCP generation
belongs exclusively to the `mcps` step (`scripts/configure-mcps.py` or
`--skip mcps` on the umbrella orchestrator).

## November 2026 model group additions

The curated Junie groups include `openai-terra` (GPT-6.1 Terra), `ollama-cloud-mimo` (MiMo V2.6 Pro primary and Flash faster), and `ollama-cloud-kimi` (Kimi K3 primary and K2.7 Code faster). The general `openai` group now prefers Terra/Luna; dedicated `openai-sol` and `openai-astra` groups remain. The naming and primary/faster contract are part of `configs/junie/model-groups.json`; the generator must not assume a model is served based on a catalogue from another provider.

`fasterModel` is a selection hint rather than a benchmark result: establish latency and tool-use success against both models in an installed Junie client before claiming performance improvement. With `DOTFILES_USE_LITELLM_PROXY=1`, the generator derives named `litellm-*` twins only where exact authenticated LiteLLM aliases are available, and must never fall back to direct credentials after a gateway failure.

This refresh did not deploy to the owner's machine. Validation of wire IDs, model discovery, credential delivery, protocol compatibility, image/tool support and generated-profile ownership is **UNKNOWN** until owner runtime probes are recorded. Restore previous source model-group entries and regenerate with `make deploy` to revert after backing up user-owned files as described above.

## Redaction hooks

Junie currently provides guidance through custom instructions only. No request-termination hook is verified; outbound LLM request redaction is not a claimed capability (**UNKNOWN-verified-none**). See the [migration plan](OPENCODE-V2-MIGRATION-PLAN.md#L67).
