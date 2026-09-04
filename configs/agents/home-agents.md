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

- **When a flag or option name is semantically ambiguous, ask before implementing.** For example, `--local-fallback-placeholder` could mean "replace the cloud model" or "replace the local fallback model." A wrong guess costs a full revert+refix cycle. Ask the user to clarify the intended semantics before dispatching implementation. Don't guess when the cost of being wrong is high.

### Delegation discipline

For changes requiring exploration of unknown scope, delegate bounded discovery first. Use direct reads for files you expect to edit, reconcile, or verify. If scope is unclear after two discovery calls, or discovery spans multiple subsystems, delegate one bounded exploration task. Request concise file:line findings, avoid full file dumps in parent context.

### Planning scope

When a feature or change touches the AI tooling fleet, assess every tool configured in the repo upfront — not just the obvious ones. If a plan covers some tools but not others, the user will ask about the missing ones. Enumerate all configured tools (OpenCode, Claude Code, Codex CLI, Gemini CLI, Cursor, VS Code Copilot, Copilot CLI, Pi, Junie, Cline, Cortex, Antigravity) in the initial plan rather than discovering them through rejection cycles.

> Skills distribution is documented in the dotfiles repo's `AGENTS.md` and `docs/ORCHESTRATION.md`.

<!-- AGENT_GUIDANCE_END -->
