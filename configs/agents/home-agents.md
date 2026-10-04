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
- Before any force-with-lease, finish the rebase and verify both a clean Git status and the expected tip. Never push mid-conflict.
- **Don't commit until the plan is approved.** If the user hasn't approved a plan or explicitly said to proceed, give the plan first. Don't pre-emptively commit work-in-progress.
- **Don't add repo artifacts for unapproved features.** This covers more than commits — don't add env vars, config entries, docs files, or other repo artifacts for a feature that hasn't been decided on. Prerequisite fixes that exist independently of the feature are fine; anything that only makes sense if the feature is chosen is not.
- All PRs created from agent work — fixes, backlog clean-ups, dependency bumps, generated changes — are opened as draft (`gh pr create --draft`) and stay draft. Never mark your own PR ready for review; a human (you, or a colleague in the downstream pass) turns the draft into a reviewable PR when they're satisfied.
- Don't auto-close a backlog issue with `Closes #N` when the PR fixes only a subset; track unresolved review threads individually.

### Verifying before declaring success

- **Lifecycle and terms.** One lane (one owned scope of work — its own branch and worktree) acts at a time; each proves its thin acceptance slice, then a single canonical integrated verification runs on the final intended state before anything ships, and finishing includes retiring your worktree. Terms used below: a **lane** is one owned scope of work; the **validation owner** is whoever independently re-runs the check; **checkpoint/stop scope** is where you pause and what ends the iteration; a **bounded stop budget** is a cap on attempts before you stop and ask the human for direction.
- At the start, agree on the outcome, a few acceptance checks, delivery state (local, draft, or merged), and permission boundaries. Choose the lightest workflow with one coherent owner; parallelize only independently owned work. Classify new requests as blocking now, next batch, or future, while respecting an explicit user override. Finalisation freezes scope: address correctness and required CI only until the agreed outcome is delivered.
- Verify honestly: claim work only when supported by actual tool results and persisted state where applicable. Never simulate tool calls, results, or transcripts. When reports conflict, actual Git/remote HEAD, index, and CI state prevail. Keep status material and factual (command, exit, HEAD, outcome); do not present status as proof. Do not claim Markdown can repair host or adapter liveness.
- For each lane, record objective, role, absolute path, base SHA, owned files, validation owner, checkpoint and stop conditions. Reuse a lane only for the same objective and role. At start, use the repository's existing runtime preflight; test the thinnest observable acceptance slice before adding harnesses or frameworks. If infrastructure dwarfs behaviour, stop and reassess the test seam. Agree on a checkpoint; seek first evidence within about five minutes when appropriate, set a bounded stop budget, and change approach after two failed attempts on the same subsystem; if it is still unresolved, stop and return to the human for a decision — do not accumulate more than three fix commits on one subsystem without an explicit scope check. A live PID or busy status is not evidence of progress. Before takeover, confirm termination and inspect actual partial state; never overlap writers or clean destructively.
- Run focused red/green checks during implementation. The validation owner independently inspects each lane and runs its decisive targeted reproduction. Then run one canonical integrated verification on the stable intended state before claiming completion, pushing or releasing. Rerun only when relevant code, inputs or runtime invalidate the evidence. Required checks and normal coverage remain mandatory; failures are blockers, not grounds to weaken global rules. For dotfiles mechanics and blocked-tool handling, use the `dotfiles-verify` skill and `docs/ORCHESTRATION.md` in the dotfiles repository.
- For service or model-generator changes, use the real consumer and credential path, capture a baseline before the first run, run twice and check health, generated state/keys and idempotence. Isolate `HOME` and `PATH`, stub real service/process commands in tests, and confirm live services survived verification.

### Tone and style

- **Mirror the conversation.** Nominally reply in the language and register the engagement is using; don't force a switch.
- **Canadian English for English prose.** Use Canadian spellings and usage (colour, centre, labelled, analytics-style -ize) in everything you write in English.
- **Canadian Press style for formal artifacts.** Reports, reviews, PR summaries, and other formal documents follow Canadian Press style (spelling, numerals, capitalization, punctuation). Casual conversation stays casual — don't formalize chat.

### Semantic ambiguity

- **When a flag or option name is semantically ambiguous, ask before implementing.** A wrong guess costs a full revert+refix cycle. Ask the user to clarify the intended semantics before dispatching implementation. Don't guess when the cost of being wrong is high.

### Focused execution and status

- Start with the decisive reproduction or check before advancing a speculative root cause. Report only material progress, a decision, or a blocker; do not narrate internal deliberation or ambient job-board state.
- When blocked, inspect persisted source and worktree state, try bounded recovery within the agreed scope, and stop for direction rather than expanding the task.

### Delegation discipline

Use bounded, independently owned lanes only when useful; inspect actual changed files and persisted state before reconciling a report. Git/worktree cleanup follows the repository lifecycle contract in the shared guidance and orchestration reference: record ownership/path/branch and creation, integration and teardown consent in the approved lane; retire unused agent-owned trees under that consent. Preserve unique committed branches. Before removing dirty saved work, make a private verified archive with restore instructions and obtain explicit dirty-removal consent. Never remove primary, user-owned or unknown trees, touch user stashes, add verification stashes, or opportunistically prune/delete branches. Directory access is not integration or deletion consent. Use the existing worktree skill's nested `.slim/worktrees` default where applicable; dotfiles sibling locations require one explicit approved exception, not a new default.

### Third-party tool claims

Before building on third-party tool behaviour (env vars, config keys, CLI flags), verify locally with the tool's introspection (`--help`, `dump-config`, `config show`, or a scratch probe); documentation can be stale. Scope capability assumptions to the specific tool/version verified, not universal claims.

For GitHub rulesets API details, see `configs/agents/repo-agents-shared.md` in the dotfiles repository: `rules` and `bypass_actors` must be JSON arrays; omit `integration_id` from `required_status_checks`; set workflow `permissions` at workflow or job level, never step level.

### Planning scope

When a feature or change touches the AI tooling fleet, assess every tool configured in the repo upfront, not just the obvious ones. The configured fleet is documented in `docs/CAPABILITY_MATRIX.md`.

> Skills distribution is documented in the dotfiles repo's `AGENTS.md` and `docs/ORCHESTRATION.md`.

<!-- AGENT_GUIDANCE_END -->
