# LiteLLM Proxy
> LiteLLM gateway configuration, keys, and environment contract.
> See [AGENTS.md](../AGENTS.md) for the lean agent guidance index.

## Gateway

The opt-in LiteLLM proxy provides a shared OpenAI-compatible model gateway on
`127.0.0.1:4000` (`LITELLM_PORT`). Enable setup with
`DOTFILES_RUN_LITELLM_SETUP=1`. Its launch service reads
`~/.local/share/litellm/service.env`, written with mode `600`. A master key is
generated with OpenSSL on first setup unless `LITELLM_MASTER_KEY` is supplied.
The gateway serves discovered chat and modality models. Direct loopback service
endpoints remain the always-available route, including when the gateway is off.

### Two-mode client contract

### Modality exposure

Discovered local models are exposed under their exact model IDs. Qualified
`audio_stt` models support `POST /v1/audio/transcriptions`; qualified

`audio_tts` models are exposed to `POST /v1/audio/speech` except
model families explicitly marked unqualified. Embedding and
rerank models are added only when discovery reports those modalities; no
placeholder aliases are created. Modality entries are kept out of the chat
catalogue. Direct loopback endpoints remain available regardless of the
LiteLLM gateway gate.

`DOTFILES_USE_LITELLM_PROXY` defaults to `0`. In direct mode, clients keep the
canonical model identity and Ollama Cloud uses `https://ollama.com/v1` with
`OLLAMA_API_KEY`; local daemon `:cloud` stubs remain a separate voice/pull
consumer and never replace a model identity. In proxy mode (`1`), routing is
gateway-only: `DOTFILES_RUN_LITELLM_SETUP=1`, each client key file, and every
exact gateway alias are required. Missing prerequisites are configuration
errors, never a direct-routing fallback.

Gateway orchestration precedes client generation. In gateway-only mode,
orchestration requires local gateway readiness and authenticated keys for each
enabled client before allowing client configuration to proceed.

Intentional direct exceptions are `meridian` (the local proxy surface, with
its own keys and responses API; it is not a LiteLLM upstream) and `copilot`
(direct GitHub OAuth in Junie only). Routing either through LiteLLM would add
a hop without attribution benefit. The doctor excludes these exceptions from
gateway-only violation checks.

Client adapters map canonical identities at their boundary through
`scripts/lib/litellm_aliases.py`; tier registries remain transport-neutral.
The old Open WebUI, OpenCode, Pi and Junie canaries migrate by OR: any active
truthy legacy value enables the new gate only when the new value is absent. An
explicit `DOTFILES_USE_LITELLM_PROXY` value always wins. Run `make migrate`
after pulling this change.

LiteLLM requires a database dependency for key, spend-tracking and UI state. A
local inference backend behind a remote SQLite/Postgres database is not
standalone for offline operation; `scripts/configure-all.sh:348` is the
reference. Set
`LITELLM_DATABASE_URL`; setup translates it to `DATABASE_URL` in `service.env`,
matching the unchanged `database_url: os.environ/DATABASE_URL` config reference.
Legacy `DATABASE_URL` remains accepted during migration, behind the new name.

### OAuth providers (opt-in)

`DOTFILES_LITELLM_OAUTH_PROVIDERS` defaults to `0`. Setting it to `1` is an
accepted operator risk: when cached tokens lapse mid-session, the proxy may
enter the interactive device flow. Cache checks and near-expiry warnings do
not prevent runtime login; withholding this gate by default is the control.
Bootstrap with `scripts/litellm-oauth.py --provider <github_copilot|chatgpt>`
in a supervised terminal.

## Client virtual keys

Each client receives its own virtual key, stored in `service.env` and materialized
under `~/.local/share/litellm/clients/`:

| Client | Environment variable | Key file |
|--------|----------------------|----------|
| OpenCode | `LITELLM_OPENCODE_KEY` | `opencode.key` |
| Pi | `LITELLM_PI_KEY` | `pi.key` |
| Open WebUI | `LITELLM_OPENWEBUI_KEY` | `openwebui.key` |
| Junie | `LITELLM_JUNIE_KEY` | `junie.key` |

## UI exposure

| Variable | Layer | Meaning |
|----------|-------|---------|
| `LITELLM_DISABLE_ADMIN_UI` | Service | Overrides LiteLLM's `DISABLE_ADMIN_UI` setting. |
| `DOTFILES_LITELLM_UI_EXPOSED` | Caddy | Controls whether the LiteLLM site is exposed through Caddy. |

These switches describe separate layers: `DOTFILES_LITELLM_UI_EXPOSED=1` exposes
the Admin UI through Caddy (with `CADDY_ACCESS=lan|public`), while the service
defaults to `LITELLM_DISABLE_ADMIN_UI=True`; set it to `False` to serve the UI.
A contradictory configuration is a doctor error.

## Listing performance

At deploy, the LiteLLM venv receives an idempotent, narrow Hugging Face bypass for `/v1/models` listing enrichment; the original `utils.py` is retained as `utils.py.orig-dotfiles`. Generated `service.env` also sets `DEFAULT_MAX_LRU_CACHE_SIZE=4096` to reduce model-info cache eviction.

## Catalogue scope

By default the generator enumerates live catalogues in a **free scope**:
only curated selections (allowlists, tier refs) plus OpenRouter ids that are
genuinely free (`:free`) are emitted. Set `DOTFILES_LITELLM_CATALOGUE_SCOPE=full`
in `~/.env` to enumerate whole live catalogues again. Any other value is a
**hard configuration error** at generation time.

## OpenAI service lanes (Flex/Fast)

`DOTFILES_USE_OPENAI_SERVICE_LANES` defaults to `0`. When enabled, the generator
expands only the genuine direct API-key `openai` route: the `OPENAI_API_KEY`
identity and canonical `https://api.openai.com/v1` base URL must both match.
OAuth/ChatGPT rows are never expanded. The curated allowlist is
`gpt-6.1-sol`, `gpt-6-astra` and `gpt-6-luna`; each receives `-flex` and `-fast`
aliases carrying request-body `service_tier` values `flex` and `fast`. A
collision with an existing alias is an error. With the gate off, rendered
output is identical to the unset-gate output. Requesting a tier does not
guarantee account access to that tier. No model-specific minimum usage-tier
prerequisite was verifiable; none is enforced here.

Lane rates are 0.5x and 2x the base rate, retaining cache-read, cache-write and
`cache_creation_input_token_cost` fields. The installed-runtime check verifies
that `service_tier` survives `drop_params=True` with LiteLLM 1.102.1; if the
check cannot verify this, setup logs an `UNVERIFIED` warning. This is bounded
runtime evidence, not a guarantee across other LiteLLM versions.

The documented standard short-context rates (input up to 272K tokens; USD per
1M tokens) are:

| Model | Input | Cached input | Cache write | Output |
|-------|------:|-------------:|-----------:|-------:|
| `gpt-6.1-sol` | $2 | $0.10 | $2.50 | $10 |
| `gpt-6-astra` | $10 | $1 | $12.50 | $50 |
| `gpt-6-luna` | $0.10 | $0.01 | $0.125 | $0.50 |

Flex is 0.5x and Fast is 2x these rates; cache fields are scaled likewise.
For input above 272K, the documented long-context rule doubles input/cache
rates and multiplies output by 1.5x.

Luna built-in tools and function calling require the Responses API. On Chat
Completions, function calling is supported only with `reasoning_effort=none`.
Astra supports function calling; no Chat Completions restriction is stated.

Policy: there is no automatic Flex-to-standard fallback, no mass role migration
to Flex, no default Fast role and no Ultrafast lane. `litellm-costs` displays the
served `service_tier` (`—` when absent); totals are not multiplied at report
time. Spend backfill skips lane aliases unless an exact per-lane rate entry
exists and reports the skipped count. Model-drift validation checks lane aliases
against the proxy inventory, never the native OpenAI catalogue. The curated
Open WebUI catalogue hides lane aliases.

The live pilot protocol is defined but deferred: after genuine credentials
exist, make one small request and verify requested versus returned tier and
cost. Use exactly one Responses-API tool call because Sol tool calls require
Responses. No genuine `OPENAI_API_KEY` is currently present on the configured
hosts (the value is an empty placeholder); keep the gate off until real
credentials are available.

## Cost rates

Request-time spend tracking resolves per-token rates from the map the proxy
actually runs. The launchd service intentionally reads the bundled local map
(`LITELLM_LOCAL_MODEL_COST_MAP=True`: single worker, internet-scrubbed, so the
remote map fetch is unavailable), and the bundled file shipped with pinned
LiteLLM lacks several current entries — which silently recorded $0.00 spend
for affected models.

Rates are generated from [models.dev](https://models.dev) at every
`make deploy`/`configure-litellm.py` run: the shared 24h-cached
`scripts/lib/models_dev.py` fetch supplies per-provider prices (models.dev
units are $/1M tokens, converted to $/token), and the generator merges them at
three precedence levels per row — OpenRouter's live-catalogue sidecar first
(request-time rates straight from upstream pricing), then the models.dev
index, then the installed bundled map. The merged document written to
`~/.local/share/litellm/model-rates.json` (diff-only, provenance = the cache's
data vintage) is what `scripts/configure-litellm-venv.py` merges into the
installed map (idempotent, first-write backed up, drift reported by the
doctor's cost-rates row); the committed `configs/litellm/model-rates.json` is
the offline/CI fallback when the runtime snapshot has not been refreshed yet.
Subscription transports (`chatgpt/*` — plan billing with no published
per-token rates) are priced at the provider's API rates as a comparable
estimate; genuinely free rows ($0 by live upstream rates), local hardware
rows, and providers with no published rates stay unpriced by design.
Context-tier pricing (`context_over_200k`) is intentionally not merged —
base rates only until a request-class-aware consumer exists.

The merge overlays managed rates onto existing entries; it must not replace
upstream capability or protocol metadata. ChatGPT subscription entries require
`mode: responses` and `litellm_provider: chatgpt`: losing those fields makes
LiteLLM send chat requests to a different upstream endpoint, which produced
Cloudflare managed challenges in the observed regression. The doctor samples
at most five exact generated ChatGPT wires with the managed interpreter,
checking cost resolution and Responses bridge mode without provider requests.
This is bounded runtime evidence, not a claim of full provider coverage or
permanent freedom from upstream challenges.

When the setup gate is enabled and the generated gateway config contains exact
`chatgpt/*` wire IDs, unavailable rate snapshots/maps or uncertain runtime
pricing evidence are blocking doctor results. If no ChatGPT wires are deployed,
unavailable pricing evidence remains a nonblocking warning. This is a
fail-closed verification policy for configured ChatGPT wires; it does not alter
model routing or require provider inference.

## Spend backfill

Spend rows recorded before pricing was wired carry the original resolved cost
($0.00 for lanes whose bundled-map entries were missing).
`python3 scripts/litellm-spend-backfill.py` reprices those historical rows
from the merged model-rate snapshot (runtime
`~/.local/share/litellm/model-rates.json` first, committed
`configs/litellm/model-rates.json` as fallback): every priced spelling
resolves to its bare models.dev key and the row's stored prompt/completion
tokens recompute `spend`. Cache-served rows are left untouched (cache pricing
cannot be reconstructed from the stored columns) and reported; genuinely
unpriced models (local hardware, keys without rates) are reported and stay.
`--dry-run` lists planned corrections without writing; updates run through
the managed venv's prisma client against the gateway database. Run it after a
`make deploy` has merged fresh rates.

## Database model-table pruning

`python3 scripts/litellm-db-prune.py` compares the served `model_name` set from
the generated `config.yaml` against the Admin API's model-table rows
(`GET /model/info`) and deletes DB-side rows the config no longer serves via
`POST /model/delete`. It never touches key, team, or spend tables. Accepts
`--dry-run` (lists planned deletions and performs no network deletes);
targets the local loopback gateway only. Useful for clearing
stale Admin UI `Models` tab rows left behind by earlier configuration states.
