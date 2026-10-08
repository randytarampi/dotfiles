# Mozart Router & Provider Configuration
> Mozart AI router gateways, unified Ollama routing, provider overrides, and JSON config conventions.
> See [AGENTS.md](../AGENTS.md) for the lean agent guidance index.

## Mozart Router Gateways

| Gateway | Adapter | API Key Env | Notes |
|---------|---------|-------------|-------|
| Ollama Cloud | GenericOpenAI | `OLLAMA_API_KEY` | Cloud-hosted models route directly through `https://ollama.com/v1`; LiteLLM client routing is controlled separately by `DOTFILES_USE_LITELLM_PROXY`. |
| openai | GenericOpenAI | `OPENAI_API_KEY` | OpenAI GPT models |
| anthropic-meridian | GenericOpenAI | `MERIDIAN_API_KEY` | Meridian proxy for Anthropic models. Host/port configurable via `MERIDIAN_HOST`/`MERIDIAN_PORT` env vars (defaults: `127.0.0.1:3456`) |
| *(local engines)* | GenericOpenAI | *(engine's `api_key_env`)* | Gate-active, reachable engines from `LOCAL_ENGINES` are appended by `configure-mozart-router.py` (registry-driven; e.g. `omlx` at `http://127.0.0.1:8000/v1` with `OMLX_API_KEY`). Deduped by base URL — a template gateway pointing at the same daemon takes precedence |

The `8000` default is upstream oMLX's own port and the repo-wide default so generated routes and a local service agree; set `OMLX_PORT` (or `OMLX_BASE_URL`) to override per machine.

Gateways support `baseUrlEnv` keys (resolved by `configure-mozart-router.py`, stripped from output). When the named env var is set, it overrides the hardcoded `baseUrl`.

`configure-mozart-router.py` is the sole writer of `~/.mozart/mozart.json` (the old `dot_mozart/mozart.json.tmpl` was removed to avoid dual-source conflicts). It resolves `baseUrlEnv` overrides at runtime before writing.

All gateways use the GenericOpenAI adapter which auto-discovers models. If an API key is not set, the gateway will be detected but connections will fail gracefully with a warning.

### Local Service Host/Port Overrides

Local services (Ollama, Meridian) support host/port env var overrides. Use `scripts/lib/constants.py` functions (`get_ollama_local_base_url()`, `get_meridian_base_url()`) which read these at runtime:

| Service | Host Env Var | Port Env Var | Default |
|---------|-------------|-------------|---------|
| Local Ollama | `OLLAMA_LOCAL_HOST` | `OLLAMA_LOCAL_PORT` | `localhost:11434` |
| Official Ollama | `OLLAMA_HOST` | — | `http://localhost:11434` | Scheme+host[:port]; overrides `OLLAMA_LOCAL_HOST`/`PORT` |
| Meridian proxy | `MERIDIAN_HOST` | `MERIDIAN_PORT` | `127.0.0.1:3456` |

#### Meridian Detection Helper

`is_meridian_configured()` in `constants.py` is the canonical way to check if Meridian proxy should be used. It returns `True` if `MERIDIAN_API_KEY` or `ANTHROPIC_BASE_URL` is set. All scripts that need to route through Meridian should use this helper instead of duplicating the detection logic.

### Canonical Ollama Cloud routing

Ollama Cloud model identities use the separate `ollama-cloud/<id>` provider and
route directly to `https://ollama.com/v1` with `OLLAMA_API_KEY` in direct mode.
When `DOTFILES_USE_LITELLM_PROXY=1`, a client adapter maps that unchanged
identity to its exact LiteLLM alias; missing gateway setup, key or alias is a
configuration error, not a direct fallback. `check_ollama_daemon()` remains a
daemon-health/sign-in probe for consumers that genuinely use installed local
`:cloud` models (notably the voice exception); it no longer changes provider
routing. Cloud stub pulls remain managed by the install script.

### Provider Base-URL Overrides

Official SDK environment variables override hardcoded provider URLs across all scripts. When set, these take priority over `BASE_URLS` defaults in `constants.py`:

| Env Var | Provider | Default | Notes |
|---------|----------|---------|-------|
| `ANTHROPIC_BASE_URL` | Anthropic | `https://api.anthropic.com/v1` | Also signals Meridian usage when set |
| `OPENAI_BASE_URL` | OpenAI | `https://api.openai.com/v1` | OpenAI SDK standard |
| `OLLAMA_CLOUD_BASE_URL` | Ollama Cloud | `https://ollama.com/v1` | Non-standard (our env var, not official SDK) |

Resolution in `constants.py` via `get_provider_base_url(provider)`: env var override → `BASE_URLS` default → local Ollama fallback.

### JSON Config Override Convention

`model-groups.json` and `mozart.json` support these override keys:

| Key | Format | Purpose |
|-----|--------|---------|
| `baseUrlEnv` | Env var name (string) | If named env var is set, overrides `baseUrl` |
| `hostEnvAlt` | Env var name (string) | Alt host env var (e.g., `OLLAMA_HOST`) with scheme+host[:port] format; takes priority over `hostEnv`/`portEnv` |

These keys are stripped from output configs (Mozart/Junie don't understand them) after resolving overrides.
