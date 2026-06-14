# Mozart Router & Provider Configuration
> Mozart AI router gateways, unified Ollama routing, provider overrides, and JSON config conventions.
> See [AGENTS.md](../AGENTS.md) for the lean agent guidance index.

## Mozart Router Gateways

| Gateway | Adapter | API Key Env | Cloud Proxy Env | Notes |
|---------|---------|-------------|-----------------|-------|
| Ollama Cloud | GenericOpenAI | `OLLAMA_API_KEY` | `DOTFILES_USE_OLLAMA_CLOUD_PROXY` | Cloud-hosted Ollama models; routes through local daemon when proxy enabled and available |
| openai | GenericOpenAI | `OPENAI_API_KEY` | — | OpenAI GPT models |
| anthropic-meridian | GenericOpenAI | `MERIDIAN_API_KEY` | — | Meridian proxy for Anthropic models. Host/port configurable via `MERIDIAN_HOST`/`MERIDIAN_PORT` env vars (defaults: `127.0.0.1:3456`) |

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

### Unified Ollama Routing

When the local Ollama daemon is running **and** signed in (`ollama signin`), it transparently proxies `:cloud` models to ollama.com. This means a single `OLLAMA_HOST` URL can serve both local and cloud models — no separate `ollama-cloud` provider needed.

#### How It Works

1. `check_ollama_daemon()` in `constants.py` probes the local daemon:
   - `GET /api/tags` → is the daemon running?
   - `GET /api/me` → is it signed in for cloud? (returns 401 if not)
   - Gated by `DOTFILES_USE_OLLAMA_CLOUD_PROXY` (default: enabled)

2. When the daemon is **cloud-capable** (running + signed in):
   - All Ollama models (local + cloud) route through `OLLAMA_HOST`
   - Cloud model names get `:cloud` suffix (e.g., `glm-5.1` → `glm-5.1:cloud`)
   - OpenCode gets a single `ollama` provider instead of separate `ollama` + `ollama-cloud`
   - SmallCode, Voice, and Mozart all route through the local daemon

3. When the daemon is **not cloud-capable** (not running, not signed in, or proxy disabled):
   - Falls back to the current two-provider approach: `ollama` (local) + `ollama-cloud` (direct)
   - Cloud models use `OLLAMA_CLOUD_BASE_URL` (default: `https://ollama.com/v1`)

#### Key Functions

| Function | Location | Purpose |
|----------|----------|---------|
| `check_ollama_daemon()` | `constants.py` | Returns `(is_running, can_proxy_cloud)` |
| `should_use_ollama_cloud_proxy()` | `constants.py` | Checks `DOTFILES_USE_OLLAMA_CLOUD_PROXY` env var |
| `get_ollama_base_url()` | `constants.py` | Returns local URL when cloud-capable, else direct cloud URL |
| `is_ollama_cloud_model(name)` | `discover_models.py` | Checks `:cloud` or `-cloud` suffix |
| `list_cloud_ollama_models()` | `discover_models.py` | Lists only cloud models from `ollama list` |

#### `:cloud` Suffix Handling

The `:cloud` suffix is appended **only** when routing through the local daemon. Direct cloud API calls use plain model names.

| Routing | Model Name | Example |
|---------|-----------|---------|
| Local daemon (cloud-capable) | `model:cloud` | `glm-5.1:cloud` |
| Direct cloud API (`ollama.com/v1`) | `model` (plain) | `glm-5.1` |
| Local model (any routing) | `model` (plain) | `qwen3-coder:14b` |

#### Scripts Affected

| Script | Change |
|--------|--------|
| `configure-opencode.py` | Merges `ollama` + `ollama-cloud` into single provider when cloud-capable |
| `configure-opencode-tier.py` | Discovers cloud models, appends `:cloud` suffixes to fallback chains |
| `configure-smallcode.py` | Routes `ollama-cloud/` models through local daemon with `:cloud` suffix |
| `configure-opencode-voice.py` | Routes voice through local daemon with `:cloud` suffix when capable |
| `configure-mozart-router.py` | Routes `ollama-cloud` gateway through local daemon when proxy enabled |

#### Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `DOTFILES_USE_OLLAMA_CLOUD_PROXY` | `1` (enabled) | Set to `0` or `false` to disable unified routing |
| `OLLAMA_HOST` | `http://localhost:11434` | Official Ollama env var for daemon URL |
| `OLLAMA_CLOUD_BASE_URL` | `https://ollama.com/v1` | Direct cloud URL (fallback when no local daemon) |

#### Disabling Unified Routing

To force the two-provider approach (separate local + cloud):

```bash
DOTFILES_USE_OLLAMA_CLOUD_PROXY=0 scripts/configure-opencode.py
```

This disables the daemon probe and always uses direct cloud URLs for cloud models.

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
| `cloudProxyEnv` | Env var name (string) | If named env var is truthy and local Ollama can proxy cloud, overrides `baseUrl` to local daemon URL and removes `apiKeyEnv` |
| `hostEnvAlt` | Env var name (string) | Alt host env var (e.g., `OLLAMA_HOST`) with scheme+host[:port] format; takes priority over `hostEnv`/`portEnv` |

These keys are stripped from output configs (Mozart/Junie don't understand them) after resolving overrides.
