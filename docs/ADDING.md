# Adding New Tiers and MCP Servers

> Process documentation for extending the dotfiles AI configuration.

---

## Adding a New Tier

1. Edit `scripts/configure-opencode-tier.py` — add a new `case` block with the tier name, preset, council config, and fallback chains (leave empty `{}` for single-provider tiers)
2. Add the preset definition to `configs/opencode/oh-my-opencode-slim.json` — define model, variant, skills, mcps, and council per agent role. For local-only tiers, use `_local:<category>` placeholders (reasoning/code-gen/lightweight/vision)
3. Add the `_tiers.<name>` block to `oh-my-opencode-slim.json` — define council agent entries, default_preset, presets, and fallback chains
4. Edit `scripts/configure-opencode.py` — add the tier to the preset validation case block, set `INCLUDE_ANTHROPIC`/`INCLUDE_OPENAI` flags as needed, configure provider generation
5. Edit `.chezmoiscripts/run_onchange_16-configure-opencode.sh.tmpl` — add tier detection logic for auto-detection
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
4. Regenerate all configs: `scripts/configure-mcps.py`

Notes:
- `idea.json` uses SSE transport by default — set `IJ_MCP_TRANSPORT=stdio` for stdio mode
- `sentry.json` passes `SENTRY_ACCESS_TOKEN` via `env`, not CLI args — don't expose tokens in command strings
- `run_onchange_15-configure-mcp.sh.tmpl` sources `detect-ij-mcp.py` output before the gate check

---

## Manual Maintenance Helpers

These scripts are useful when you want to refresh runtime-generated files without waiting for a full orchestration pass:

- `scripts/configure-secrets.py` — resolves secrets and `.env`-derived paths; called by `configure-all.sh`, but also useful as a standalone refresh step
- `scripts/configure-jetbrains-workspace-project.py` — updates JetBrains workspace module AI directories; manual-only and not wired into configure-all.sh because it needs explicit workspace/project paths
- `scripts/configure-meridian.py` — refreshes Meridian proxy config for OpenCode
- `scripts/configure-jetbrains-ai.py --all` / `--all-tools` — regenerates JetBrains AI models, dirs, symlinks, and MCP wiring

See [docs/ORCHESTRATION.md](ORCHESTRATION.md) for the three-layer architecture and when to use these helpers.

---

## Hash Triggers

Use the standard chezmoi hash comment to make `run_onchange_*` scripts re-run when inputs change:

```bash
# <path>: {{ include "<path>" | sha256sum }}
```

Add hash triggers whenever a configure script depends on:
- config fragments in `configs/`
- generated manifests or templates in `scripts/`
- any other file whose contents affect generated output

If a generated artifact is stale after a pull, check the relevant hash trigger first. Then run `make check-hashes` and, if needed, `make reset && make deploy`.

See [docs/ORCHESTRATION.md](ORCHESTRATION.md) for the canonical hash-trigger policy.

---

## Adding a New Configure Script

1. Create `scripts/configure-foo.py` — keep it idempotent and follow the `configure-*` naming pattern
2. Create `run_onchange_NN-configure-foo.sh.tmpl` with hash triggers for its inputs
3. Add the script to `scripts/configure-all.sh` in dependency order
4. Add a `DOTFILES_RUN_*_SETUP` gate to `.env.example` if the script is opt-in
5. Add the generated output files to `scripts/verify-config.py`
6. Run `make check-hashes` to verify coverage
7. Run `make verify` to confirm the new script fits the orchestration flow

**Gate renames:** When renaming a gate, add a migration entry to `scripts/migrate-env-gates.py` `MIGRATIONS` list. See [docs/ORCHESTRATION.md](ORCHESTRATION.md) "Gate Migrations" section.
