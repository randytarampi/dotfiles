# oMLX Integration

oMLX is an Apple-Silicon MLX model server for macOS. It exposes OpenAI-compatible
and Anthropic-compatible APIs, including `/v1/chat/completions`, `/v1/responses`,
`/v1/messages`, embeddings, reranking, and audio endpoints. It is **not** an
Ollama-wire-compatible server: it has no `/api/*` routes and no `pull` CLI.
Models are MLX-converted safetensors directories under `~/.omlx/models`, managed
through the Hugging Face admin UI.

## Platform and gate

The local oMLX service is supported when script 29's actual gate passes: Darwin
on arm64 (Apple Silicon); script 29 skips other platforms. The Homebrew formula builds from source and is
declared unconditionally in `Brewfile.dev`, alongside other development tools.
The gate controls settings/service setup and consumer/provider generation. A
configured remote oMLX endpoint may be consumed on other platforms when gated;
that is an intentional, platform-agnostic remote-server path.

The integration gate is:

```sh
DOTFILES_RUN_OMLX_SETUP=1
```

The default is `0`. With the gate off there are no oMLX providers, routes,
discovery results, or generated consumer entries; installation itself remains
managed by the unconditional Brewfile entry.

## Installation and service

The formula is declared in `Brewfile.dev`:

```ruby
tap "jundot/omlx", "https://github.com/jundot/omlx"
brew "jundot/omlx/omlx"
```

`run_onchange_29-configure-omlx.sh.tmpl` writes and merges
`~/.omlx/settings.json`, then starts the service with `brew services`. The
persisted settings use the nested oMLX schema:
`server`, `model`, `memory`, `scheduler`, `cache`, `auth`, and `huggingface`.
The Homebrew service plist does not inherit `~/.env`, so API authentication is
persisted as `auth.api_key` when configured. The settings file and backups use
0600 permissions.

```sh
make omlx-restart
make services-restart
```

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `DOTFILES_RUN_OMLX_SETUP` | `0` | Enable oMLX installation wiring, service, and consumers. |
| `OMLX_HOST` | `127.0.0.1` | Bind host for the server. |
| `OMLX_PORT` | `8000` | Bind port for the server. |
| `OMLX_BASE_URL` | — | Endpoint override; origin only, without `/v1`. |
| `OMLX_MODEL_DIR` | `$HOME/.omlx/models` | MLX model directory. |
| `OMLX_MEMORY_GUARD` | `balanced` | Memory tier; `off` sets `prefill_memory_guard=false`, other tiers set `memory_guard_tier`. |
| `OMLX_SSD_CACHE_DIR` | `$HOME/.omlx/cache` | Persistent SSD KV-cache directory. |
| `OMLX_MAX_CONCURRENT_REQUESTS` | `8` | Scheduler concurrency. |
| `OMLX_API_KEY` | _(unset)_ | Intentionally unset on deployed machines: with no key oMLX sets
  `skip_api_key_verification` and binds loopback only, so it is reached solely
  through Caddy's `/omlx/*` route. Set it to restore external server aliases +
  per-key auth. |
| `OMLX_HF_ENDPOINT` | — | Optional Hugging Face endpoint; persisted when set. |
| `OMLX_LOG_LEVEL` | `info` | CLI/environment input; persisted as `server.log_level`. |
| `OMLX_WIRED_LIMIT_MB` | `40960` | Metal wired limit in MB (Apple Silicon). Script 29 runs `configure-omlx-wired-limit.py` to apply `sudo sysctl iogpu.wired_limit_mb=<limit>` and install the root `com.dotfiles.omlx-wired-limit` LaunchDaemon that re-applies it at boot. Apple's default cap (~75% of RAM) rejects model loads above it with HTTP 507; 40960 MB fits a 38 GB model such as a 35B 4-bit on a 48 GB Mac. Values above 90% of physical RAM are refused. |

`OMLX_BASE_URL` takes precedence over `OMLX_HOST`/`OMLX_PORT`. Its value is an
origin such as `http://127.0.0.1:8000`; consumers append `/v1` themselves.

### Env-authoritative vs admin-UI-managed keys

`merge_omlx_settings` (Script 29) writes a fixed set of **env-authoritative**
keys into `~/.omlx/settings.json`; all other keys are preserved from the
on-disk file and remain **admin-UI-managed** (tuned in the dashboard).

**Env-authoritative** (the repo `~/.env` value wins on every deploy):
`server.host`, `server.port`, `server.log_level`, `memory.*`,
`scheduler.max_concurrent_requests`, `cache.*`, `mcp.expose_tools`, and
`model.model_dirs` (via `OMLX_MODEL_DIR`).

**Admin-UI-managed** (preserved as-is): `mcp.config_path`,
`huggingface.endpoint`, per-model `model_settings.json` (TurboQuant KV), and any
oMLX key not listed above.

**No-API-key mode (loopback only):** when `OMLX_API_KEY` is unset, the writer
also forces `server.skip_api_key_verification = true` and restricts
`server.server_aliases` to `["127.0.0.1", "localhost"]` with empty
`server.cors_origins`, so the admin UI and API are reachable only through Caddy's
`/omlx/*` route — never directly on an external alias. Restoring `OMLX_API_KEY`
re-enables external server aliases and per-key auth (both then admin-UI-managed).

### Cache parity

The oMLX `CacheSettings` block is the MLX equivalent of Ollama cache tuning.
MLX has no global KV-cache quantization knob, but TurboQuant KV is per-model
(`~/.omlx/model_settings.json`, engine-construction field — reload/restart to
apply). `OLLAMA_KV_CACHE_TYPE` drives both daemons from one canonical name:
`q8_0` enables TurboQuant at 8 bits, `q4_0` at 4 bits, and `f16`/unset leaves
per-model settings untouched (admin-UI managed). Script 29 applies the mapping
and restarts the service when models change. Persistent SSD and optional
hot-cache settings provide the comparable residency and reuse controls. The
service plist does not inherit `~/.env`, so these values are persisted in
`~/.omlx/settings.json`.

| Environment variable | Settings key | Default |
|----------------------|--------------|---------|
| `OMLX_CACHE_ENABLED` | `cache.enabled` | `true` |
| `OMLX_SSD_CACHE_DIR` | `cache.ssd_cache_dir` | `~/.omlx/cache` |
| `OMLX_SSD_CACHE_MAX_SIZE` | `cache.ssd_cache_max_size` | `auto` |
| `OMLX_HOT_CACHE_MAX_SIZE` | `cache.hot_cache_max_size` | `0` (disabled) |
| `OMLX_HOT_CACHE_WRITE_THROUGH` | `cache.hot_cache_write_through` | `false` |
| `OMLX_INITIAL_CACHE_BLOCKS` | `cache.initial_cache_blocks` | `256` |

### MCP

oMLX can act as an MCP aggregator: it loads MCP servers from an `mcp.json`
(configured in the dashboard under Settings → MCP, persisted as
`mcp.config_path`) and serves them at `/v1/mcp/tools`, `/v1/mcp/servers`, and
`/v1/mcp/execute`. There are two independent consumers of that backend:

- The dashboard Chat UI is its own MCP client: it fetches `/v1/mcp/tools` and
  executes calls via `/v1/mcp/execute` directly. It does **not** depend on the
  expose toggle, so configuring the Config Path gives Chat UI tools without
  exposing anything to API clients.
- API clients receive backend MCP tools merged into completions only when
  `mcp.expose_tools` is true (the upstream default is `true`).

The repository pins `mcp.expose_tools = false` in the managed settings writer
(unconditional — a first-run file or an admin flip converges back to off):
every fleet consumer (OpenCode, Codex, ACP, Junie, Pi, Gemini) already runs
its own MCP client, and backend-merged tools would duplicate entries and
muddle tool selection on every completion. Escape hatch:
`OMLX_MCP_EXPOSE_TOOLS=1`. `mcp.config_path` stays admin-UI-managed — set it
in the dashboard to a file such as `~/.omlx/mcp.json` (format: `{"servers":
{...}}`, Claude Desktop `mcpServers` also accepted; requires a server
restart). Security: the Caddy LAN route blocks `/admin*` and `/v1/mcp/*`
(`scripts/lib/local_engines.py`), so MCP execution stays loopback-only.

## Provider integration

### Engine-agnostic local pool

“Local” means the combined pool of all models served by configured local engines;
selection does not distinguish Ollama from oMLX. Adding another engine such as
LM Studio requires a discovery module plus one `LOCAL_ENGINES` registry entry
covering its endpoint, metadata, audio, and Caddy contracts, plus hash triggers,
tests, and any relevant allowlists; consumers iterate
those registry contracts and remain unchanged. Model drift checking is also
automatically covered by the registry dispatch. Per-engine app pinning belongs
to that engine's launcher, for example `omlx launch codex`, `omlx launch claude`,
or `omlx launch hermes`. Generated provider blocks remain separate because each
endpoint needs its own base URL.

| Tool | oMLX integration |
|------|------------------|
| OpenCode | Gated, reachable provider for all tier classes; uses `omlx/<id>` references. |
| Tier resolution | Merged `_local` pool; oMLX wins engine-equivalent bare-name collisions. |
| Junie | One selectable profile per chat-capable pool model (`local-<provider>-<slug>.json`), generated registry-driven with an OpenAI-compatible endpoint; `fasterModel` chains to the same engine's next-ranked model. |
| Pi | oMLX provider alongside Ollama; native `max_model_len` context is used. |
| ACP agents | `claude--local` and `codex--local` are pool-driven; the winning engine supplies the endpoint and authentication follows that engine's configuration. |
| Codex | Pool-driven winner per profile plus one provider per gate-active engine — `codex --model <any-id>` resolves against the engine's `base_url`, so every pool model is selectable without re-configuring. Pin an app with the engine's own launcher. |
| Voice | oMLX `audio_stt` is selected below explicit OpenAI STT tiers; `DOTFILES_USE_LOCAL_OMLX=false` opts out. TTS remains Piper and Pi voice is unchanged. |
| Mozart router | Gate-active, reachable engines are appended as `generic-openai` gateways by `configure-mozart-router.py` (deduped by base URL; API key via the engine's `api_key_env`). With oMLX running, the deployed `mozart.json` carries an `omlx` gateway. |

## Caddy

When enabled, `/omlx/*` is a read-only reverse proxy to the local oMLX service.
Administrative and mutating endpoints are blocked. The route is gated by
`DOTFILES_RUN_OMLX_SETUP=1`. With no `OMLX_API_KEY` configured, oMLX binds
loopback only and skips its own API key, so Caddy is the sole external entry
point and carries `basic_auth` for `lan`/`public` access modes.

## Capability mapping

| oMLX metadata | Repository capability |
|---------------|-----------------------|
| `model_type=llm` or `vlm` | `completion` and optimistic `tools` |
| `thinking_default` or `enable_thinking` | `thinking` |
| `model_type=vlm` | `vision` |
| `model_type=audio_stt/audio_tts/audio_sts` | `audio` |
| `embedding`, `reranker`, or unknown type | Fail-closed: excluded from chat role pools |
| Missing `is_moe` metadata | Unknown-safe density ranking; names with an `A#B` marker are treated as MoE, otherwise dense. |

Tool support is optimistic for language and vision models because their served
API is tool-capable; this has a documented false-positive risk when upstream
metadata does not expose an override field.

## oMLX and Ollama parity

| Capability | oMLX | Ollama |
|------------|------|--------|
| SSD-persistent KV cache | Yes | Not this integration's default |
| Continuous batching | Yes | Yes |
| Anthropic API | Yes | No direct `/v1/messages` surface |
| Audio endpoints | Yes | Not assumed by this integration |
| Admin UI | Yes | No equivalent used here |
| Pull CLI | No; use HF/admin management | Yes |
| `/api/*` routes | No | Yes |
| GGUF ecosystem / Modelfile | No | Yes |
| Windows and Linux | No | Yes |

## Model management

Keep oMLX and Ollama model sets **disjoint**. Both services can coexist under
the oMLX gate, but duplicating large models increases memory contention on a
128 GB workstation. oMLX models are MLX safetensors directories, not GGUF files:

```text
~/.omlx/models/<model>/config.json
~/.omlx/models/<model>/*.safetensors
```

Download and manage models through the admin UI at
`http://<OMLX_HOST>:<OMLX_PORT>/admin` or the supported Hugging Face workflow.
The repository does not pull models or manage model directories.

## Not managed

- The oMLX menu-bar app and native app lifecycle.
- Cluster or multi-Mac mode (experimental).
- Custom Metal kernels, which require full Xcode tooling.
- `omlx launch <tool>` integrations; repository configure scripts supersede them.
- The embeddings API: it is documented and exposed, but has no repository consumer today.
