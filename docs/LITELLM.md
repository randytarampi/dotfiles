# LiteLLM Proxy
> LiteLLM gateway configuration, keys, and environment contract.
> See [AGENTS.md](../AGENTS.md) for the lean agent guidance index.

## Standalone Claude CLI

`scripts/claude-litellm` is an explicit standalone launcher; it does not
replace `claude`, alter Claude settings, or configure Claude Desktop/Cowork or
the Claude ACP adapter. It requires `DOTFILES_RUN_CLAUDE_SETUP=1`,
`DOTFILES_RUN_LITELLM_SETUP=1` and `DOTFILES_USE_LITELLM_PROXY=1`, plus a provisioned
`~/.local/share/litellm/clients/claude.key` with mode `0600`. It launches Claude
with `--bare`, disables user/project/local setting sources, sets the gateway
root endpoint and an explicitly configured model in session-only settings, and supplies the key through
`ANTHROPIC_API_KEY` without putting it in arguments or settings. The model
`DOTFILES_CLAUDE_LITELLM_MODEL` is required; no default alias is assumed. Set
it only to an exact model alias already qualified as served.
The dedicated local key is restricted to `http://127.0.0.1:${LITELLM_PORT}`
(port defaults to 4000), with no path. `DOTFILES_CLAUDE_LITELLM_BASE_URL`,
if supplied, must equal that root exactly; remote destinations are refused
before the key is read. Remote gateways require a separate credential and
an explicitly approved contract, not reuse of this local key.

The wrapper strips inherited Anthropic credentials and refuses direct
endpoint/model/settings switches, but unmanaged policy may still affect
provider behaviour. The installed CLI version's transport, key handling and
exact gateway model alias require a live check before relying on this adapter;
no inference qualification is claimed here.

## Claude Desktop third-party gateway (export scaffold only)

`scripts/export-claude-desktop-gateway.py` is an explicit, standalone exporter;
it is not part of deployment or automatic client configuration. Supply the
loopback endpoint, every exact served model ID, and an absolute helper path:

```sh
scripts/export-claude-desktop-gateway.py \
  --endpoint http://127.0.0.1:4000 \
  --model '<exact-served-model-id>' \
  --helper '/absolute/path/to/credential-helper'
```

It writes a macOS configuration profile (mobileconfig plist) to stdout only.
Review it and separately install/apply it only after owner consent. The profile
contains endpoint/model identifiers and the helper path as a reference; it
contains no API key and does not verify that the helper exists, runs, or
delivers a gateway credential. Export does not inspect or modify Claude's
private settings, configuration library, managed preferences, or credentials;
it does not launch Claude or a helper, provision keys, change defaults, or make
network/inference requests. Endpoint input is restricted to a root
`http://127.0.0.1:<port>` URL; `/v1` is not appended.
The top-level `Configuration` profile contains one `PayloadContent` preference
payload of type `com.anthropic.claudefordesktop`. Within that payload, gateway
preferences are flat keys: `inferenceGatewayBaseUrl`,
`inferenceModels` (a JSON array string of `{ "name": "<exact-id>" }` objects),
`inferenceCredentialKind=helper-script`, `inferenceCredentialHelper` (the path
string), `inferenceGatewayAuthScheme=bearer`, and
`inferenceProvider=gateway`.

The official gateway contract requires streaming and Messages/tools
compatibility; arbitrary LiteLLM backends are not asserted compatible. Models
must be explicitly named; no defaults or capability claims are inferred.
Third-party configuration covers Desktop Chat, Cowork, and Code, but
acceptance for each remains UNKNOWN. Local history changes and hosted-mode
behaviour are separate considerations. Managed settings take precedence over
local settings; the documented return path is the Anthropic sign-in option on
the sign-in screen, subject to deployment policy. This is a separate deployment
mode with local conversation history, not a migration of hosted chat history.
This export does not establish credential delivery or equate Desktop with the
Claude CLI's environment-variable configuration.

References: [Desktop gateway contract](https://claude.com/docs/third-party/claude-desktop/gateway),
[configuration encoding](https://claude.com/docs/third-party/claude-desktop/configuration-format),
and [single-machine setup](https://claude.com/docs/third-party/claude-desktop/installation#single-machine-setup).

## Gateway

The opt-in LiteLLM proxy provides a shared OpenAI-compatible model gateway on
`127.0.0.1:4000` (`LITELLM_PORT`). Enable setup with
`DOTFILES_RUN_LITELLM_SETUP=1`. Its launch service reads
`~/.local/share/litellm/service.env`, written with mode `600`. A master key is
generated with OpenSSL on first setup unless `LITELLM_MASTER_KEY` is supplied.
The gateway serves discovered chat and modality models. Direct loopback service
endpoints remain the always-available route, including when the gateway is off.
The `DOTFILES_RUN_LITELLM_SETUP` gate defaults to `0`; enabling it provisions
the local gateway.

### Two-mode client contract

`DOTFILES_USE_LITELLM_PROXY` defaults to `0`. In direct mode, clients keep the
canonical model identity and Ollama Cloud uses `https://ollama.com/v1` with
`OLLAMA_API_KEY`; local daemon `:cloud` stubs remain a separate voice/pull
consumer and never replace a model identity. In proxy mode (`1`), routing is
gateway-only: `DOTFILES_RUN_LITELLM_SETUP=1`, each client key file, and every
exact gateway alias are required. Missing prerequisites are configuration
errors, never a direct-routing fallback.
This proxy switch is independent of the setup gate and defaults to `0`; setting
it to `1` without the required setup and client prerequisites fails closed.

In gateway mode, a previously selected local-owner model (`ollama/`, `omlx/`,
or `llamacpp/`) without a matching served alias is marked UNRESOLVED with the
exact missing alias and this operator instruction: re-run generation once the
alias exists. The gateway-policy-breaking direct selection is refused for
installation and the generation step fails through its critical error path.
The prior Slim generation file is kept unmodified only as a recovery artifact;
it is never silently preserved into the newly installed config. A new direct
local-owner route without a served alias is likewise refused.

Gateway orchestration precedes client generation. In gateway-only mode,
orchestration requires local gateway readiness and authenticated keys for each
enabled client before allowing client configuration to proceed.

Intentional direct exceptions are `meridian` (the local proxy surface, with
its own keys and responses API; it is not a LiteLLM upstream — client-specific:
Junie separately derives authenticated gateway twins for its routed Meridian
groups, see docs/JUNIE.md) and `copilot`
(direct GitHub OAuth in Junie only). Routing either through LiteLLM would add
a hop without attribution benefit. The doctor excludes these exceptions from
gateway-only violation checks.

GitHub Copilot CLI has a separate, opt-in launcher at `scripts/copilot-litellm`;
it is distinct from the Copilot extension identity. Both
`DOTFILES_RUN_COPILOT_SETUP=1` and `DOTFILES_USE_LITELLM_PROXY=1` are required.
Set `DOTFILES_COPILOT_LITELLM_MODEL` to an exact served alias; no model is
assumed. The launcher uses the Copilot CLI's installed OpenAI `completions`
BYOK contract and the dedicated
`~/.local/share/litellm/clients/copilot-cli.key`. Bearer tokens, key commands,
custom headers and inherited model selectors are cleared. The default endpoint
is loopback LiteLLM `/v1`; an explicit
`DOTFILES_COPILOT_LITELLM_BASE_URL` override is available. Installed CLI help
confirms this configuration surface, but a live request is not qualified and
remains UNKNOWN; this does not imply ACP or IDE support.
The wrapper forwards a bounded set of session options, including prompt,
resume, output, reasoning and tool-approval options; unsupported flags fail
with a usage error rather than being guessed from help text. In addition to
`DOTFILES_RUN_COPILOT_SETUP=1` and `DOTFILES_USE_LITELLM_PROXY=1`,
`DOTFILES_RUN_LITELLM_SETUP=1` is required. Each gate defaults to `0` and is
independent; enabling the Copilot adapter and proxy does not enable gateway
provisioning. Endpoint overrides remain loopback-only.
The launcher fails closed whenever `COPILOT_PROVIDERS_CONFIG` is set (even to
an empty value) or `~/.copilot/providers.json` exists in any form. It checks
only the file's presence and never reads, edits or deletes it: the installed
CLI gives provider declarations precedence over the legacy environment surface,
so their effective routing cannot be safely assumed. Review/manage that
configuration separately using supported Copilot CLI routing before using this
adapter; no temporary configuration or precedence override is created. Any set
`COPILOT_HOME` is also refused, including an empty value, because its config
location is not established. If the default `.copilot` directory cannot be
searched to establish that `providers.json` is absent, the adapter refuses to
continue. Dry-run
validates gates, model, endpoint, arguments, CLI presence and this file safety
gate, but reports credential availability as UNKNOWN without reading the key
or launching the CLI. A successful preview is not a credential or routing
readiness claim.

Client adapters map canonical identities at their boundary through
`scripts/lib/litellm_aliases.py`; tier registries remain transport-neutral.
The old Open WebUI, OpenCode, Pi and Junie canaries migrate by OR: any active
truthy legacy value enables the new gate only when the new value is absent. An
explicit `DOTFILES_USE_LITELLM_PROXY` value always wins. Run `make migrate`
after pulling this change.

### Modality exposure

Discovered local models are exposed under their exact model IDs. Qualified
`audio_stt` models support `POST /v1/audio/transcriptions`; probe qualification
is not a consumer outcome. Qualified `audio_tts` models are exposed to
`POST /v1/audio/speech` except model families explicitly marked unqualified.
Embedding, rerank and
conversion models — e.g. MarkItDown on the oMLX engine serving
`POST /v1/chat/completions` — are added only when discovery reports those
modalities; no placeholder aliases are created. Modality entries are kept out
of the chat catalogue. Direct loopback endpoints remain available regardless
of the LiteLLM gateway gate.

WebUI document ingestion through MarkItDown is intentionally NOT wired:
WebUI's external document loader speaks a raw-body + `X-Filename` protocol
that no MarkItDown deployment serves; bridging it would need a separate
adapter service (deferred until required). See docs/CHAT_FRONTEND.md.

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

When `DOTFILES_RUN_CODEX_SETUP=1` and `DOTFILES_USE_LITELLM_PROXY=1` are
enabled, the generator materializes the managed `model_providers.litellm`
block, reading `LITELLM_CODEX_KEY` with key file
`~/.local/share/litellm/clients/codex.key`; provisioning uses the same explicit
client-key authorization and gateway setup contract as other clients. The
managed shell configuration exports that file's contents as `LITELLM_CODEX_KEY`
only when `DOTFILES_RUN_CODEX_SETUP=1`, `DOTFILES_USE_LITELLM_PROXY=1` and
`DOTFILES_RUN_LITELLM_SETUP=1`, and the file exists. This file-export gate does
not clear or otherwise isolate an inherited `LITELLM_CODEX_KEY` when any gate is
off; manually supplied environment values are preserved. Export applies only
to Codex processes launched from a shell that has sourced that managed
configuration. This does not establish credential delivery for GUI, ACP or
other-shell launches and has not been live-verified. Codex's
provider `env_key` reads it from the process environment. The generated named `litellm` profile is
explicit opt-in and does not change the active default. Pointing Codex's
default at LiteLLM — `model_provider = "litellm"` in
`~/.codex/config.toml` — is deliberately left to the operator: switching the
active default changes request routing and is treated as an owner-controlled,
destructive-category action, not written automatically.

The repo-side route is classified `eligible` — provisioned like the other
gateway clients — but `classification_status` records it as **not
live-verified**: reaching `verified` requires an owner-provided dedicated key,
a real routed task, and two full-deploy runs with marker-integrity checks. These
remain owner-gated; fresh-subprocess authentication proof is NOT RUN/UNKNOWN
here. A gateway `key_alias` is attribution-only, not a model-restricted
privilege unless separately enforced.
Those qualifications are not performed by repository tests or configuration
generation.

Codex running inside ChatGPT Desktop and ordinary ChatGPT conversations are
different consumers. This route configures the Codex process only; ordinary
ChatGPT conversations are not wired through it.

Its existing
`~/.codex/config.toml` may contain sections written by an older generator without
ownership markers. Back up the file privately, review the specifically reported
colliding sections, and manually remove or rename only sections you approve as
legacy before rerunning configuration. Preserve ambiguous sections and ask the
owner to resolve them; the generator refuses to overwrite them. Do not delete
all matching section names or migrate them automatically.

The standalone `scripts/configure-codex.py` entrypoint requires the locked
project runtime. `scripts/configure-all.sh` invokes it through Poetry rooted at
the dotfiles checkout, so it works when called from another directory. Python 3.10 receives the
locked `tomli` dependency while Python 3.11 and later use their standard-library
`tomllib`. Do not run the script with a bare Python 3.10 interpreter. If
Poetry is missing, install Poetry first. If the Poetry environment is missing
dependencies, install the locked production runtime with
`poetry install --only main --no-root` rather than using global `pip`. Configure
does not install dependencies automatically; Codex runtime preflight is skipped
for `--dry-run` and `--skip codex`.

Before removing or renaming a section, review top-level `model_provider`,
provider references inside profiles and the selected profile so they do not
refer to a removed section; the generator does not repair these references.

| Client | Environment variable | Key file |
|--------|----------------------|----------|
| OpenCode | `LITELLM_OPENCODE_KEY` | `opencode.key` |
| Pi | `LITELLM_PI_KEY` | `pi.key` |
| Open WebUI | `LITELLM_OPENWEBUI_KEY` | `openwebui.key` |
| Junie | `LITELLM_JUNIE_KEY` | `junie.key` |
| Codex | `LITELLM_CODEX_KEY` | `codex.key` |

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
curated catalogue selections plus routes classified free by a model name containing
`free`, or Cohere/Poolside when served by that provider itself (directly or
through this gateway). Third-party hosted Cohere/Poolside routes do not qualify.
This catalogue enumeration scope is separate from the disabled `free` preset:
including a model in the free-scope catalogue does not enable a preset or prove
request-time cost. OpenRouter `:free` twins are included by default; set
`DOTFILES_LITELLM_INCLUDE_OPENROUTER_FREE_TWINS=0` to disable them.
Automatic OpenRouter twin enumeration requires the exact `:free` suffix;
this narrower catalogue rule is distinct from general name-based free
classification, which matches `free` anywhere in a model ID.
`DOTFILES_LITELLM_CATALOGUE_SCOPE=full` enumerates whole live catalogues. Any
other scope value is a **hard configuration error** at generation time. Free
classification is metadata and does not guarantee request-time no-charge
admission or prevent paid fallbacks.

Run `PYTHONPATH=scripts/lib poetry run python -m free_classifier` for the
offline tracked-selection gate (exit 1 on violations). It resolves shared
Junie free groups and their gateway twins with the production group helpers;
this is not a claim that profiles were deployed or selected in the IDE.
The Python audit API can accept explicit provider endpoint configurations and
LiteLLM model-list records. It checks every backend of an alias and keeps
Junie's primary and faster providers distinct. Missing endpoint provenance
is unknown, not an OpenCode/Cohere/Poolside exemption. Name-based `free`
classification remains the owner's separate rule, independent of host.
The default gate has no deployed route records and remains blocked by
unresolved provenance and the retained Google assignments. It is not wired
into `make verify`; a passing unit suite does not mean this gate passes or
that #87's request-time admission, entitlement, privacy or paid-route-spy
requirements are satisfied.

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

### Hermes configuration scaffold

`scripts/configure-hermes.py --model <served-alias>` adds the namespaced
`providers.dotfiles-litellm` entry to `$HERMES_HOME/config.yaml` when both
`DOTFILES_RUN_HERMES_SETUP=1` and `DOTFILES_USE_LITELLM_PROXY=1`. The endpoint
defaults to `http://127.0.0.1:${LITELLM_PORT:-4000}/v1`; `--endpoint` accepts
only that loopback `/v1` form. Hermes' default provider is not changed; select
the scaffold explicitly with `hermes --provider dotfiles-litellm --model
<served-alias>`.

The schema was inspected in Hermes 0.22.4 (`hermes_cli/config_providers.py`):
`name`, `api`, `transport: chat_completions`, `key_env` and `default_model`.
The generator stores only the `LITELLM_HERMES_KEY` environment-variable name,
never its value. Hermes' runtime provider resolver gives saved credential-pool
credentials precedence when no explicit key is supplied; the installed CLI
does not expose a verified explicit-key selector. Therefore the scaffold is
configuration-only: credential delivery and live LiteLLM transport remain
unqualified, with provisioning deferred.

Without `HERMES_HOME`, the generator targets `~/.hermes/config.yaml`.
`--dry-run` previews without writing; `--no-backup` explicitly skips the
otherwise exclusive mode-0600 `config.yaml.bak`. An existing backup is never
overwritten. Existing matching provider entries are a no-op; differing entries,
duplicate keys, aliases, flow collections and empty/null provider mappings are
refused rather than rewritten. Back up and resolve those forms manually before
retrying. Unmanaged text and global selections are preserved.
