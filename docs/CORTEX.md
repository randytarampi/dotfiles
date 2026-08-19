# Snowflake Cortex Code

Cortex Code is Snowflake's terminal coding agent and a Snowflake specialist—not a general provider-agnostic agent. It requires a Snowflake account, the `SNOWFLAKE.CORTEX_USER` role, `connections.toml`, and the `snow` CLI.

## Files

| File | Purpose |
|---|---|
| `~/.snowflake/cortex/settings.json` | Compact mode, updates, theme, and model |
| `~/.snowflake/cortex/permissions.json` | Permission defaults |
| `~/.snowflake/cortex/mcp.json` | Native Cortex MCP servers |
| `~/.snowflake/cortex/hooks.json` | Optional Cortex hooks |
| `~/.snowflake/cortex/skills/` | Global Cortex skills |
| `~/.snowflake/connections.toml` | Shared Snowflake CLI connections |

Set `SNOWFLAKE_HOME` to override the default `~/.snowflake` directory. The model defaults to `auto`; set `CORTEX_AGENT_MODEL` to choose a supported Cortex model such as `claude-*` or `gpt-*`.

## Install and configure

Enable `DOTFILES_RUN_CORTEX_SETUP=1` and run `make deploy` or `make configure`. The integration gracefully skips when `connections.toml` or `snow` is unavailable. Installation uses Snowflake's macOS/Linux curl installer (or the Windows PowerShell installer), and verifies `cortex --version`.

## ACP and MCP

The native ACP server is `cortex acp serve -c <connection_name>`. Set `CORTEX_CONNECTION` for the generated OpenCode ACP entry, then invoke it as a delegated specialist. Cortex MCP is deliberately separate from the global MCP registry; add servers with:

```sh
cortex mcp add <name> <commandOrUrl>
```

## Skills and usage

Skills are linked to `~/.snowflake/cortex/skills/` and can also be project-local in `.cortex/skills/`. Delegate Snowflake-specific SQL, schemas, warehouses, and Cortex tasks through the `subagent-cortex-code` specialist rather than using Cortex for unrelated general coding.

Common tasks:

```sh
cortex -c <connection_name>
cortex acp serve -c <connection_name>
DOTFILES_RUN_CORTEX_SETUP=1 make configure
make verify
```
