# Paseo ACP provider discovery and owner registration

`scripts/configure-paseo-acp.py` extends existing `configure-acp-agents.py`
command contracts. Native Paseo agents (OpenCode, Claude, Codex, Pi, Antigravity)
are not re-registered; Hermes retains its independent opt-in script.

`python3 scripts/configure-paseo-acp.py --dry-run` lists host-observed
candidate availability and planned changes without writing. Review the actual
installed CLI and published ACP arguments, then quiesce the Paseo Desktop,
daemon, and all config writers. With owner approval:
`DOTFILES_RUN_PASEO_ACP_SETUP=1 python3 scripts/configure-paseo-acp.py --apply --agents gemini,junie`.

No automatic registration is included in `make deploy`. Missing binaries,
a user-owned divergent entry, Cortex's connection placeholder, malformed or
non-private settings, and non-absolute PATH resolutions fail closed.
The existing ACP wrapper registry describes candidates, **not** proof of a
working ACP session. Install/authenticate agents outside this script and
verify an actual `initialize`, streaming task, tool approval and denial,
cancellation, reconnection and workspace use.

The script stages all native `paseo daemon config set` changes before apply,
and compares the entire resulting JSON document. This does **not** solve the
upstream lost-update race tracked in #173. Do not apply concurrently, and
never auto-restore a stale snapshot on failure. Remove only an exact managed
entry through an owner-approved native `config unset` after taking a backup.

Owner: Randy Tarampi (requirements and acceptance); author: ChatGPT (GPT-6),
implementation and tests. Reviewer and live host proof: pending.
