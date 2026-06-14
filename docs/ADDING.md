# Adding New Tiers and MCP Servers

> Process documentation for extending the dotfiles AI configuration.

---

## Adding a New Tier

1. Edit `scripts/configure-opencode-tier.py` — add a new `case` block with the tier name, preset, council config, and fallback chains (leave empty `{}` for single-provider tiers)
2. Add the preset definition to `configs/opencode/oh-my-opencode-slim.json` — define model, variant, skills, mcps, and council per agent role. For local-only tiers, use `_local:<category>` placeholders (reasoning/code-gen/lightweight/vision)
3. Add the `_tiers.<name>` block to `oh-my-opencode-slim.json` — define council agent entries, default_preset, presets, and fallback chains
4. Edit `scripts/configure-opencode.py` — add the tier to the preset validation case block, set `INCLUDE_ANTHROPIC`/`INCLUDE_OPENAI` flags as needed, configure provider generation
5. Edit `.chezmoiscripts/run_once_14-configure-opencode.sh.tmpl` — add tier detection logic for auto-detection
6. Update `AGENTS.md` tier table — add the new tier row
7. Update `README.md` tier table — add the new tier row
8. Update `configs/opencode/anthropic-models.json` if adding new Anthropic model IDs
9. Test: `scripts/configure-opencode-tier.py <tier>`, then `/preset <name>` in OpenCode

`configure-opencode-tier.py` is the single source of truth for tier→preset mapping.

---

## Adding an MCP Server

1. Create `configs/mcp/<server>.json` — MCP config JSON for the server
2. Add entry in `configs/mcp/global-mcps.json` — register which AI tools should receive this server config
3. Test with a single tool: `scripts/configure-mcp-tool.py <tool> <server>`
4. Regenerate all configs: `scripts/configure-mcp-all.py`

Notes:
- `idea.json` uses SSE transport by default — set `IJ_MCP_TRANSPORT=stdio` for stdio mode
- `sentry.json` passes `SENTRY_ACCESS_TOKEN` via `env`, not CLI args — don't expose tokens in command strings
- `run_once_13-configure-mcp.sh.tmpl` sources `detect-ij-mcp.py` output before the gate check
