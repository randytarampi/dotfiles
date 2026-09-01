# Adding New Tiers and MCP Servers

> Process documentation for extending the dotfiles AI configuration.

---

## Adding a New Tier

1. Add the tier's preset and `_tiers.<name>` block to `configs/opencode/oh-my-opencode-slim.json` — define roles, variants, skills, MCP references, council, and fallback chains. Add provider model IDs to the matching `configs/opencode/*-models.json` allowlist. For local-only tiers, use `_local:<category>` placeholders (reasoning/code-gen/lightweight/vision)
2. Update `scripts/lib/tier_registry.py` if the tier requires new shared resolution behavior. This registry is consumed by OpenCode, Junie, and Pi and is the single source of truth for tier → role → model mapping.
3. Edit `scripts/configure-opencode.py` or `.chezmoiscripts/run_onchange_16-configure-opencode.sh.tmpl` only when provider generation or tier auto-detection needs an independent update
4. Update `AGENTS.md` and `README.md` tier tables
5. Update `configs/opencode/anthropic-models.json` if adding new Anthropic model IDs
6. Test: `scripts/configure-opencode-tier.py --preset <tier>`, then `/preset <name>` in OpenCode

Use the required `--preset <tier>` flag; positional tier arguments are not supported. Tier details belong in [docs/TIERS.md](TIERS.md).

---

## Adding an MCP Server

1. Create `configs/mcp/<server>.json` — MCP config JSON for the server
2. Add entry in `configs/mcp/global-mcps.json` — register which AI tools should receive this server config
3. Test with a single tool: `scripts/configure-mcp-tool.py --dry-run <tool>` (use `--show-secrets` to print resolved values; `<tool>` is a registry key like `cursor`, `claude_desktop`, `vscode`)
4. Regenerate all configs: `scripts/configure-mcps.py`

Notes:
- `idea.json` uses SSE transport by default — set `IJ_MCP_TRANSPORT=stdio` for stdio mode
- `sentry.json` passes `SENTRY_ACCESS_TOKEN` via `env`, not CLI args — don't expose tokens in command strings
- `run_onchange_15-configure-mcp.sh.tmpl` sources `detect-ij-mcp.py` output before the gate check

### Formats

Each tool in `global-mcps.json` declares a `format` that `configure-mcp-tool.py` uses to render and merge the config. Choose based on the target app's schema:

| Format | Output shape | Merge behavior | Used by |
|---|---|---|---|
| `json-mcpServers` | `{"mcpServers": {name: {command, args, env, [headers], [enabled]}}}` | Overwrite entire file | ai, air, cursor, junie (dedicated MCP-only files) |
| `json-mcpServers-merge` | Same as `json-mcpServers` | Top-level JSON merge (preserves other keys) | claude_desktop (shared app config file) |
| `json-servers` | `{"servers": {name: {type: stdio\|http, command, args, env, url, headers}}}` | Top-level JSON merge | vscode (VS Code `servers` schema) |
| `toml-mcpServers` | `[mcp_servers.NAME]` blocks | Regex strip + append | codex |
| `opencode-internal` | `{"mcp": {name: {type, command, environment, headers, enabled}}}` | Deep-merge `mcp` key | opencode |
| `json-settings-merge` | `{"mcpServers": {...}}` | Top-level JSON merge | gemini |

Use `json-mcpServers-merge` (not `json-mcpServers`) when the target file is a shared app config that contains other top-level keys (e.g. `claude_desktop_config.json` has `coworkUserFilesPath`, `preferences`). The plain `json-mcpServers` format overwrites the entire file and would clobber those keys.

### Per-tool constraints

Some tools cannot consume the full template set. Check the target app's documented schema before adding a template to a tool:

- **Claude Desktop** (`claude_desktop_config.json`) is **stdio-only**. Its parser is gated by a Zod schema that rejects `url`/`type`/`transport` fields; passing them causes the app to silently destroy the entire `mcpServers` block and strip `preferences` keys on save (see anthropics/claude-code#37286). Exclude the `idea` template (IntelliJ streamable-HTTP) from `claude_desktop`. Remote MCP for Claude Desktop is managed via the Connectors UI or the `mcp-remote` stdio bridge, not the config file.
- **ChatGPT Desktop** has no local MCP config file — MCP is configured via workspace/UI connectors. Do not add it to `global-mcps.json`.
- **VS Code** uses the `servers` top-level key (not `mcpServers`) with an explicit `type: stdio|http` field — use the `json-servers` format. It supports both stdio and HTTP servers.

---

## Manual Maintenance Helpers

These scripts are useful when you want to refresh runtime-generated files without waiting for a full orchestration pass:

- `scripts/configure-secrets.py` — resolves secrets and `.env`-derived paths; called by `configure-all.sh`, but also useful as a standalone refresh step
- `scripts/configure-meridian.py` — refreshes Meridian proxy config for OpenCode
- `scripts/configure-jetbrains-ai.py` — regenerates JetBrains AI model profiles, dirs, and symlinks (MCPs are generated separately by the `mcps` step)

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

## Adding a Skills Category

Create `configs/skills/skills.<name>.json` with a unique profile and its skill
entries. Omit `gate` for always-on categories such as `core` and `mattpocock`.
For opt-in categories, add the corresponding category skills setup gate to
`dot_dotfiles/shell/.env.example` and the gate table in
[ORCHESTRATION.md](ORCHESTRATION.md). Add the category file to the hash triggers
in `.chezmoiscripts/run_onchange_28-configure-skills.sh.tmpl`, then run
`make check-hashes` and `make verify`.

## Project-Scoped Skills and Configuration

Create `.opencode/.env` in a project and select categories independently of global
gates:

```bash
DOTFILES_PROJECT_SKILL_PROFILES=core,aws,mongodb
DOTFILES_PROJECT_SKILLS=plannotator-review
DOTFILES_PROJECT_SKIP_SKILLS=handoff
```

Run `scripts/configure-project.py --steps skills`. CLI flags override the project
file. Selected skills are symlinked from the canonical cache into
`.opencode/skills/`, and stale project links are removed.

`.opencode/.env` is user-authored project configuration and is never overwritten.
Generated secrets go to `.opencode/.env.local` (gitignored), loaded after `.env`.
See [project-env.example](project-env.example) for the complete template.

`configure-project.py` supports `opencode`, `tier`, `codegraph`, `mcps`, `skills`,
`jetbrains`, `junie`, `acp-agents`, and `secrets` steps. Defaults to
`opencode,codegraph,skills,jetbrains,junie`; use `--steps` to run any subset,
`DOTFILES_PROJECT_STEPS` to override the default set from `.opencode/.env`, or
the per-step `DOTFILES_PROJECT_*` variables to opt individual default steps out
(set them false-y, e.g. `DOTFILES_PROJECT_JUNIE=0`). JetBrains and Junie project
work delegates to the existing scripts.

All configure scripts use the `--skip STEP[,STEP...]` umbrella where applicable;
use it to omit named steps such as `mcps`. `configure-all.sh` also accepts
`--local-fallback-preset`, `--local-fallback-role`,
`--local-fallback-placeholder`, `--preset`, `--mode`, and
`--min-reasoning-embedding`.

## Adding a new agent tool

When adding a new agent tool to the fleet:

1. **Fleet registry**: Add an entry to `scripts/lib/fleet-registry.json` with
   telemetry disposition, voice support, i18n mechanism, and guidance path.
2. **Telemetry**: If the tool supports a local opt-out, add env vars to
   `dot_dotfiles/shell/.env.example` (uncommented if effective). Document the
   disposition in `docs/TELEMETRY.md`.
3. **Guidance**: If the tool reads instruction files, add its path to
   `AGENT_FILES` in `scripts/configure-agent-guidance.py` and to the path list
   in `scripts/verify-config.py`.
4. **Capability matrix**: Add a row to the table in `docs/CAPABILITY_MATRIX.md`
   including the new Telemetry, Voice, and i18n columns.
5. **Verify**: Run `make check-fleet-coverage` to confirm the registry matches
   the actual repo state, then `make verify` for full validation.
