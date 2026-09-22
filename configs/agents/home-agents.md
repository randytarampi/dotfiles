# Home Agent Guidance

Cortex Code is Snowflake's specialist terminal agent configured in `~/.snowflake/cortex/`; use it for Snowflake-specific work, not general provider-agnostic tasks.

Configured agent tools are OpenCode, Codex, Junie, Pi, Cortex, Claude,
Copilot, Gemini, Cursor, Cline, and Antigravity. Their integration details
are documented in `docs/CAPABILITY_MATRIX.md` in the dotfiles repository.

<!-- Managed by configure-agent-guidance.py — do not edit between AGENT_GUIDANCE markers -->

<!-- AGENT_GUIDANCE_START -->
## Working with me

These apply to every repo, every session.

### Commits

- **One concern per commit.** When closing out a session, commit each logical change individually — never batch unrelated changes into a single commit. If a session touched three concerns, that's three commits.
- **Never push unless explicitly asked.** Default to local commits only. "Don't push anything yet" is the standing instruction; the user will say when to push.
- **Don't commit until the plan is approved.** If the user hasn't approved a plan or explicitly said to proceed, give the plan first. Don't pre-emptively commit work-in-progress.
- **Don't add repo artifacts for unapproved features.** This covers more than commits — don't add env vars, config entries, docs files, or other repo artifacts for a feature that hasn't been decided on. Prerequisite fixes that exist independently of the feature are fine; anything that only makes sense if the feature is chosen is not.

### Verifying before declaring success

- **Run the repo's standard verify command before claiming a change is done.** Don't report "done" or "working" based on reasoning alone — execute the actual check.
  - dotfiles: `make verify`
  - other repos: whatever the repo defines (`yarn test`, `yarn lerna run <job>`, `npm run build`, the repo's Makefile target, etc.)
- If the verify command fails, fix it before reporting success. Don't hand back work that the user will immediately find broken by running the same command themselves.
- Skip this only for docs-only or trivially mechanical changes (whitespace, typos, renames) where verification adds no signal.

### Tone and style

- **Mirror the conversation.** Nominally reply in the language and register the engagement is using; don't force a switch.
- **Canadian English for English prose.** Use Canadian spellings and usage (colour, centre, labelled, analytics-style -ize) in everything you write in English.
- **Canadian Press style for formal artifacts.** Reports, reviews, PR summaries, and other formal documents follow Canadian Press style (spelling, numerals, capitalization, punctuation). Casual conversation stays casual — don't formalize chat.

### Semantic ambiguity

- **When a flag or option name is semantically ambiguous, ask before implementing.** A wrong guess costs a full revert+refix cycle. Ask the user to clarify the intended semantics before dispatching implementation. Don't guess when the cost of being wrong is high.

### Delegation discipline

For changes requiring exploration of unknown scope, delegate bounded discovery first. Use direct reads for files you expect to edit, reconcile, or verify. If scope is unclear after two discovery calls, or discovery spans multiple subsystems, delegate one bounded exploration task. Request concise file:line findings, avoid full file dumps in parent context.

Never run two write-capable subagent lanes that commit concurrently to one repository, even with disjoint file scopes: git staging and HEAD are process-global, so parallel commits race — work is lost to staging conflicts, finished edits strand in stashes, and commit boundaries cross-contaminate. Dispatch committing lanes one at a time, serialize their commits from the orchestrator, or isolate parallel writers in separate git worktrees.

Dispatch background specialists before ending a turn when user input may arrive: a foreground or in-turn dispatch loses in-flight specialist work when the turn is interrupted. After an interruption, verify the repository's tip and dirty state directly before re-dispatching (a specialist's stashes and partial commits are recoverable only after verification — three incidents on 2026-09-22).

### Third-party tool claims

Before building on a third-party tool's documented behaviour (env vars, config keys, CLI flags), verify it locally with the tool's own introspection (`--help`, `dump-config`, `config show`, a scratch-directory probe) — research claims can be wrong or version-stale, and a two-minute probe beats a wrong implementation (2026-09-22: chezmoi silently ignores `CHEZMOI_CONFIG`; qlty silently ignores `qlty.toml` `[[plugin]] exclude_patterns` — bandit scoping belongs in `.bandit`, which the driver actually reads).

### GitHub rulesets API notes

- Verified live 2026-09-22 (dotfiles rulesets 23831217/23837328): `rules` and `bypass_actors` must be real JSON arrays in the rulesets REST call — `-F 'key[0]=...'` form-encoding yields 422 "not of type array"; `required_status_checks` entries must OMIT `integration_id` entirely (explicit `null` returns 422 "data matches no possible input"); the job-level `permissions` key is valid only at job level in workflow YAML, not step level (actionlint catches it); `qlty config show` reveals a plugin's actual driver invocation (e.g. bandit reads `--ini .bandit`).

### Planning scope

When a feature or change touches the AI tooling fleet, assess every tool configured in the repo upfront — not just the obvious ones. If a plan covers some tools but not others, the user will ask about the missing ones. Enumerate all configured tools (OpenCode, Claude Code, Codex CLI, Gemini CLI, Cursor, VS Code Copilot, Copilot CLI, Pi, Junie, Cline, Cortex, Antigravity) in the initial plan rather than discovering them through rejection cycles.

> Skills distribution is documented in the dotfiles repo's `AGENTS.md` and `docs/ORCHESTRATION.md`.

<!-- AGENT_GUIDANCE_END -->
