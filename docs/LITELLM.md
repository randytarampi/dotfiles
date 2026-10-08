# LiteLLM Proxy
> LiteLLM gateway configuration, keys, and environment contract.
> See [AGENTS.md](../AGENTS.md) for the lean agent guidance index.

## Gateway

The opt-in LiteLLM proxy provides a shared OpenAI-compatible model gateway on
`127.0.0.1:4000` (`LITELLM_PORT`). Enable setup with
`DOTFILES_RUN_LITELLM_SETUP=1`. Its launch service reads
`~/.local/share/litellm/service.env`, written with mode `600`. A master key is
generated with OpenSSL on first setup unless `LITELLM_MASTER_KEY` is supplied.

### Two-mode client contract

`DOTFILES_USE_LITELLM_PROXY` defaults to `0`. In direct mode, clients keep the
canonical model identity and Ollama Cloud uses `https://ollama.com/v1` with
`OLLAMA_API_KEY`; local daemon `:cloud` stubs remain a separate voice/pull
consumer and never replace a model identity. In proxy mode (`1`), routing is
gateway-only: `DOTFILES_RUN_LITELLM_SETUP=1`, each client key file, and every
exact gateway alias are required. Missing prerequisites are configuration
errors, never a direct-routing fallback.

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

LiteLLM requires Postgres for key, spend-tracking, and UI state. Set
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

## Database model-table pruning

`python3 scripts/litellm-db-prune.py` compares the served `model_name` set from
the generated `config.yaml` against the Admin API's model-table rows
(`GET /model/info`) and deletes DB-side rows the config no longer serves via
`POST /model/delete`. It never touches key, team, or spend tables. Accepts
`--dry-run` (lists planned deletions and performs no network deletes) and
`--no-backup`; targets the local loopback gateway only. Useful for clearing
stale Admin UI `Models` tab rows left behind by earlier configuration states.
