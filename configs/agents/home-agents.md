# Home Agent Guidance

<!-- Managed by configure-agent-guidance.py — do not edit between AGENT_GUIDANCE markers -->

<!-- AGENT_GUIDANCE_START -->
## Working with me

These apply to every repo, every session.

### Commits

- **One concern per commit.** When closing out a session, commit each logical change individually — never batch unrelated changes into a single commit. If a session touched three concerns, that's three commits.
- **Never push unless explicitly asked.** Default to local commits only. "Don't push anything yet" is the standing instruction; the user will say when to push.
- **Don't commit until the plan is approved.** If the user hasn't approved a plan or explicitly said to proceed, give the plan first. Don't pre-emptively commit work-in-progress.

### Verifying before declaring success

- **Run the repo's standard verify command before claiming a change is done.** Don't report "done" or "working" based on reasoning alone — execute the actual check.
  - dotfiles: `make verify`
  - other repos: whatever the repo defines (`yarn test`, `yarn lerna run <job>`, `npm run build`, the repo's Makefile target, etc.)
- If the verify command fails, fix it before reporting success. Don't hand back work that the user will immediately find broken by running the same command themselves.
- Skip this only for docs-only or trivially mechanical changes (whitespace, typos, renames) where verification adds no signal.

## Dotfiles Repo Development

When working on the dotfiles repo itself:
- **Before committing:** `make verify` (lint + drift + doctor + check-hashes + dry-run)
- **After pulling:** `make deploy` (full rebuild — chezmoi apply + configure-all.sh)
- **Adding a configure script:** Wire it into both a `run_onchange_*` chezmoi script AND `configure-all.sh`. Add hash triggers for its config inputs.
- **Adding a gate:** Use `DOTFILES_RUN_*_SETUP` pattern. Document in `.env.example`. Default to `0`.
- **Script conventions:** `run_once_*` for one-time ops only, `run_onchange_*` for everything else. See `AGENTS.md` Scripting Conventions for full policy.
- **Architecture:** Three layers — templates, chezmoi scripts, configure scripts. See `docs/ORCHESTRATION.md` for the canonical reference.

## Skills Distribution

Skills are managed declaratively via a manifest at `configs/skills/skills.json` (analogous to Brewfile/wingetfile for packages). The script `scripts/configure-skills.py` reconciles installed skills against the manifest:
1. Fetches missing skills via the `skills` CLI (`vercel-labs/skills`) into `~/.agents/skills/` (canonical store)
2. Symlinks each skill to all 8 agent skill directories (`.agents`, `.config/opencode`, `.claude`, `.gemini`, `.codex`, `.cursor`, `.ai`/`.junie`, `.hermes`)
3. Removes stale skills not in the active manifest
4. Supports optional profiles gated on `DOTFILES_RUN_*` env vars

The `skills` CLI only installs to 4 agent dirs; `configure-skills.py` extends coverage to all 8.

### Manifest format

The manifest (`configs/skills/skills.json`) declares skills by source repo and name:
```json
{ "source": "owner/repo", "name": "skill-name" }
```
Skills are grouped into profiles:
- **global** — always active, ~114 skills covering AWS, MongoDB, Prisma, TypeScript dev workflow, content/media, Plannotator, CodeGraph, iamhumans, agent meta
- **macos** — Apple ecosystem (apple-notes, apple-reminders, findmy, imessage), gated on `DOTFILES_RUN_MACOS_SKILLS_SETUP=1`, macOS only

### iamhumans

The [iamhumans](https://github.com/hoainho/iamhumans) skill provides a humanization layer for LLM conversation. It's fetched via `skills add hoainho/iamhumans --global -y` — no copy-paste needed. Full `references/` tree is included.

### Updating skills

- **Update all**: `make skills-update` or `skills update --global -y`
- **Update one**: `skills update <skill-name> --global -y`
- **Add a skill**: Add entry to `configs/skills/skills.json`, then `make deploy`
- **Remove a skill**: Remove entry from manifest, then `make deploy` (stale skill gets removed)
- **Lock file**: `~/.agents/.skill-lock.json` tracks installed state

> [!IMPORTANT]
> Skill names in the manifest must match the `name` field in `~/.agents/.skill-lock.json` exactly. Before editing `skills.json`, list installed names with `jq -r '.skills[].name' ~/.agents/.skill-lock.json`. Guessed names (e.g. `aws-messaging` vs the real `aws-messaging-and-streaming`) cause `skills add` to fail silently.

### Gate

- `DOTFILES_RUN_SKILLS_SETUP=1` — enables global skills distribution (default: 0)
- `DOTFILES_RUN_MACOS_SKILLS_SETUP=1` — enables macOS-only Apple ecosystem profile (default: 0)

### `skills` CLI

The [`skills`](https://www.skills.sh) CLI (`vercel-labs/skills`) fetches skills from GitHub repos:
```bash
skills add owner/repo/skill-name --global -y   # Install a skill
skills ls -g                                     # List global skills
skills update --global -y                        # Update all skills
skills find "humanization"                       # Search registry
```

Install via `brew install skills` (macOS/Linux) or `npm install -g skills` (all platforms).

### lazyskills CLI

`lazyskills` is a CLI tool for discovering and managing skills from the registry:

```bash
lazyskills find --json "humanization"
lazyskills list
lazyskills info iamhumans
```

Install via `scripts/install-skills.sh` or manually: `brew install --cask alvinunreal/tap/lazyskills` (macOS), `curl -fsSL https://lazyskills.sh/install | sh` (Linux), `irm https://lazyskills.sh/install.ps1 | iex` (Windows).

## ACP Agent Verification

ACP (Agent Client Protocol) agents are configured in `~/.config/opencode/acp-agents.json` (gitignored, generated by `scripts/configure-acp-agents.py`). To verify ACP agent setup:

1. **Check config**: `cat ~/.config/opencode/acp-agents.json` — should contain `acpAgents` with entries for configured agents
2. **Check merge**: ACP agents are merged into `oh-my-opencode-slim.json` during `configure-opencode.py` — verify with `grep acpAgents ~/.config/opencode/oh-my-opencode-slim.json`
3. **Restart OpenCode**: After any ACP config change, restart OpenCode for agents to load
4. **Authenticate**: Each ACP agent requires its own auth:
   - **Copilot**: `copilot auth` (or set `GITHUB_TOKEN`)
   - **Claude**: `claude /login` (claude-code-acp wraps claude)
   - **Codex**: OpenAI auth (codex-acp wraps codex)
   - **Gemini**: `GEMINI_API_KEY` env var or `gemini auth`
   - **Junie**: JetBrains IDE login
   - **Antigravity**: `agy auth login` (Google Sign-In via system keyring). Then enable the bridge: set `DOTFILES_RUN_ANTIGRAVITY_ACP_SETUP=1` and `make deploy`.

     > [!CAUTION]
     > Google's Antigravity ToS prohibit using third-party software to access the Service. Routing Antigravity OAuth through the `antigravity-acp` bridge *may lead to account suspension*. Use Vertex AI / AI Studio API keys instead if this risk is unacceptable.
5. **Test**: In OpenCode, use `/agent <agent-name>` to invoke a specific ACP agent (e.g., `@gemini hello` or `@copilot explain this code`)

## Tokenscope

`@ramtinj95/opencode-tokenscope` is an OpenCode plugin for analyzing token usage and costs:

- **Install**: `opencode plugin @ramtinj95/opencode-tokenscope@latest --global` (via `scripts/install-opencode.sh`)
- **Usage**: Run `/tokenscope` in OpenCode UI to analyze token usage and costs for the current session
- **Config**: Added to the `plugin` array in `opencode.json` by `scripts/configure-opencode.py`
<!-- AGENT_GUIDANCE_END -->
