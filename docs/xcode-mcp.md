# Xcode MCP — optional macOS fleet registration

Xcode's `xcrun mcpbridge` exposes tools through MCP stdio, separate from the existing IntelliJ MCP server. The repository now defines a mirrored `xcode` template and opt-in registration for existing configured MCP clients. Nothing is generated on ordinary deploy when `DOTFILES_RUN_XCODE_MCP_SETUP` is unset.

Before enabling: install a compatible version of Xcode, select its developer directory, open a project, and manually enable **Xcode → Settings → Intelligence → Allow external agents to use Xcode tools**. No setup script changes Xcode permissions. `xcrun --find mcpbridge` proves only the executable is discoverable, not that a session/permission handshake will succeed.

With owner approval, on macOS set `DOTFILES_RUN_XCODE_MCP_SETUP=1` and `DOTFILES_RUN_MCP_SETUP=1`, then preview `python3 scripts/configure-mcps.py --mode global --dry-run`. Inspect per-client formatting and existing user-owned entries before normal `configure-mcps.py` apply. The generator refuses opt-in on non-macOS hosts, unknown `xcrun`, and absent bridge instead of silently removing a desired entry. Do not run the bridge on a network listener or publish it via Caddy.

Owner-only acceptance requires MCP initialize, one read-only project inspection, explicit denial of a mutating action, installed-client launch, and two deployments retaining unrelated MCP state. Withdrawing the gate does not automatically delete a previously installed user-owned entry; reconcile the client setting explicitly after comparison.

Author: ChatGPT (GPT-6). Owner: Randy Tarampi. Installed Xcode, Intelligence permission and client acceptance: NOT RUN.
