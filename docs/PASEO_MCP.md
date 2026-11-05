# Paseo MCP server — owner-only activation

Paseo's `daemon.mcp.enabled` and `daemon.mcp.injectIntoAgents` have
different security implications. The MCP server can expose agent, workspace,
terminal and schedule operations, whereas injection adds these tools to each
newly launched agent.

This script only proposes `daemon.mcp.enabled=true` with
`daemon.mcp.injectIntoAgents=false`. It refuses pre-existing user-disabled
settings and any enabled injection. There is no unattended `make deploy`
integration or implicit privilege escalation. To review the proposed config:
`python3 scripts/configure-paseo-mcp.py --dry-run`.

Owner-only apply (after quiescing all native config writers and backing up
private state): `DOTFILES_RUN_PASEO_MCP_SETUP=1 python3 scripts/configure-paseo-mcp.py --apply`.
The native Paseo CLI is rehearsed in a private copy first and checked after
each live setting. This **does not fix the lost-update race in #173**.
Restart the daemon; verify an authenticated MCP connection, one permitted
read-only status query and denial of unauthorised actions.

Do not enable injection globally until the installed provider-specific
`agents.providers.*.paseoTools` policy and session permission boundaries
are reviewed. A tool catalogue restriction is not a security sandbox for a
coding agent with shell access. Rollback only the exact owner-applied settings;
never overwrite a newer unrelated configuration snapshot.

Authored by ChatGPT (GPT-6); requirements and host acceptance: Randy Tarampi.
Host MCP connection and approvals: not tested.


### Owner-only no-injection acceptance

After enabling on a private, authenticated daemon, call MCP `list_agents` and verify an unauthorised request is denied. Launch a **new** agent and inspect its offered tools; with `daemon.mcp.injectIntoAgents=false`, none of the privileged Paseo MCP catalogue (`list_agents`, `create_agent`, `kill_agent`) may be injected. A successful MCP handshake alone does not qualify this boundary.
