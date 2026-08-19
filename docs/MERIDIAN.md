# Meridian Proxy

Meridian is a local Anthropic-compatible proxy/router at `http://127.0.0.1:3456/v1`, also an OpenCode plugin (`@rynfar/meridian`). Gated by `DOTFILES_RUN_MERIDIAN_SETUP=1`. Uses Claude Code SDK OAuth (not `ANTHROPIC_API_KEY`).

For Mozart router config (a separate component), see [MOZART.md](MOZART.md).

## SDK Feature Toggles

Meridian persists per-adapter SDK feature toggles in `~/.config/meridian/sdk-features.json`. The OpenCode adapter is configured with:

```json
{
  "opencode": {
    "codeSystemPrompt": false,
    "clientSystemPrompt": true
  }
}
```

### `codeSystemPrompt: false`

Disables injection of Claude Code's base system prompt (~28 KB). OpenCode supplies its own system prompt via `clientSystemPrompt: true`, so Claude receives only OpenCode's prompt — no duplicate framing.

This is safe because:
- OpenCode runs in passthrough mode (executes its own tools)
- Tool configuration is assembled separately from the system prompt
- Session continuity uses `x-opencode-session` headers, not the preset
- DCP compression travels as part of OpenCode's client prompt
- Memory, dreaming, `claudeMd`, thinking, and connectors are all disabled

If memory or dreaming are enabled later, reevaluate this setting — Meridian recommends the preset for those features.

### Changing the setting

Via API:
```bash
curl -X PATCH http://127.0.0.1:3456/settings/api/features/opencode \
  -H 'Content-Type: application/json' \
  -d '{"codeSystemPrompt":false}'
```

Or via Meridian web UI at `http://127.0.0.1:3456/settings` under **SDK Feature Toggles**.

Settings are read at request time and cached for ~5 seconds. No restart required, but avoid changing mid-session — start a fresh session after toggling.

## Sonnet Context Tier

Meridian supports two Sonnet context tiers:

| Model string | Context window | When to use |
|---|---|---|
| `sonnet` (default) | 200K | Routine work — lower cost |
| `sonnet[1m]` | 1M | Deliberate repo-wide analysis — billed as Extra Usage on Max plans |

Selection via environment variable:

```bash
MERIDIAN_SONNET_MODEL=sonnet      # 200K (default)
MERIDIAN_SONNET_MODEL=sonnet[1m]  # 1M
```

Subagents always use `sonnet` (200K) regardless of this setting.

### OpenCode / Meridian context sync

OpenCode and Meridian **must agree** on the context window size. If OpenCode declares 1M to DCP but Meridian serves 200K, percentage thresholds calculate against the wrong window (e.g., 33% of 1M = 330K, but if Meridian serves 200K, that threshold is unreachable).

Verify alignment by checking:
- `~/.cache/opencode/models.json` — what OpenCode declares to DCP
- `MERIDIAN_SONNET_MODEL` env var — what Meridian actually serves

## Configuration Scripts

- `scripts/configure-meridian.py` — appends Meridian plugin path to `opencode.json` plugin array. Also manages `~/.config/meridian/sdk-features.json` (ensures `opencode.codeSystemPrompt=false`, preserving other adapter settings).
- `scripts/configure-codex.py` — adds Meridian as an available provider in `~/.codex/config.toml` (base_url, wire_api, env_key). Codex defaults to OpenAI; switch to Meridian with `codex -c model_provider=meridian -m <model>`. Preserves existing runtime settings.
- `scripts/meridian-launch.sh` — launches Meridian proxy, unsets `ANTHROPIC_API_KEY`/`ANTHROPIC_BASE_URL`.
- `.chezmoiscripts/run_onchange_11-install-meridian-launchd.sh.tmpl` — installs launchd plist for Meridian.
