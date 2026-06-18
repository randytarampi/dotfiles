# Home Agent Guidance

<!-- Managed by configure-agent-guidance.py — do not edit between AGENT_GUIDANCE markers -->

<!-- AGENT_GUIDANCE_START -->
## Dotfiles Repo Development

When working on the dotfiles repo itself:
- **Before committing:** `make verify` (lint + drift + doctor + check-hashes + dry-run)
- **After pulling:** `make deploy` (full rebuild — chezmoi apply + configure-all.sh)
- **Adding a configure script:** Wire it into both a `run_onchange_*` chezmoi script AND `configure-all.sh`. Add hash triggers for its config inputs.
- **Adding a gate:** Use `DOTFILES_RUN_*_SETUP` pattern. Document in `.env.example`. Default to `0`.
- **Script conventions:** `run_once_*` for one-time ops only, `run_onchange_*` for everything else. See `AGENTS.md` Scripting Conventions for full policy.
- **Architecture:** Three layers — templates, chezmoi scripts, configure scripts. See `docs/ORCHESTRATION.md` for the canonical reference.

## CodeGraph Integration

[CodeGraph](https://github.com/colbymchenry/codegraph) is a local-first semantic code index + MCP server.

- **Zero-config**: No config file, no API keys, fully local
- **MCP server**: `codegraph serve --mcp` (stdio transport)
- **Parent-walk**: CodeGraph automatically walks up from CWD to find `.codegraph/` in parent directories. A project in `~/Development/dotfiles` will use `~/Development/.codegraph/` if no local index exists.
- **`projectPath` parameter**: All CodeGraph MCP tools accept a `projectPath` parameter to query a specific indexed project. Use this only when parent-walk won't find the right index (sibling/unrelated directories). The value is the directory containing `.codegraph/`, not the `.codegraph/` directory itself. `projectPath` overrides parent-walk.
- **Fallback rule**: Always try CodeGraph tools first. If they return empty results, fall back to grep/glob/read. Do not retry CodeGraph for the same query.
- **Installation**: If codegraph MCP tools are unavailable (server failed to start), install: `npm i -g @colbymchenry/codegraph`
- **⚠️ Bare `codegraph` triggers the interactive installer** — use `codegraph status`, `codegraph init`, `codegraph install`, etc.

| Situation | Action |
|-----------|--------|
| Working in an indexed project | Nothing — parent-walk finds it |
| Working in a subdirectory of an indexed parent | Nothing — parent-walk finds it |
| Need to query a sibling project's index | Pass `projectPath` to that project's root |
| No `.codegraph/` anywhere in ancestor chain | Pass `projectPath` to a known indexed directory, or fall back to grep/glob |

**EMFILE troubleshooting**: If you see `EMFILE: too many open files, watch` errors in `~/.codegraph/daemon.log` on large indexes, either:
- Increase system limits: `sudo sysctl -w kern.maxfiles=65536 kern.maxfilesperproc=65536`
- Or disable file watching: use `codegraph serve --mcp --no-watch` in your MCP config (rebuild index manually with `codegraph init` after changes)

Available steps for `configure-opencode-project.py --steps`: `opencode` (always), `tier`, `codegraph` (opt-in), `mcps`. Default: `opencode,tier`. Run `codegraph init` manually in any directory you want to index.
<!-- AGENT_GUIDANCE_END -->
