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

- **How we ship**
  1. **Agree:** set the outcome, acceptance checks, delivery state and permissions. Classify new requests as blocking, next batch or future; honour explicit overrides and freeze scope at finalisation except for correctness and required CI.
  2. **Own:** one writer owns one branch and worktree. Record objective, role, path, base SHA, files, validation owner, checkpoint and stop conditions. Parallel lanes are allowed only with disjoint, recorded ownership.
  3. **Prove:** run the repository's runtime preflight and test the thinnest observable acceptance slice before building harnesses. Seek first evidence within about five minutes when appropriate.
  4. **Stop:** after two failed approaches on one subsystem, change approach; if still unresolved, stop and ask the human. Do not start a fourth fix commit without an explicit scope check.
  5. **Claim:** report only actual tool results and persisted state; never simulate calls, results or transcripts. Git/remote HEAD, index and CI prevail over conflicting reports; a status or live PID is not proof.
  6. **Verify:** the validation owner inspects each lane and re-runs its decisive check; then run one canonical integrated verify before completion, push or release. Dotfiles use `make verify`; other repos use their documented command. Verify the intended committed state, not unrelated dirty-worktree content; for agreed local uncommitted delivery, label and verify that exact tree.
  7. **Finish:** deliver the agreed state and retire agent worktrees under recorded consent. Agent PRs stay draft; do not push, mark ready or merge without explicit authority.
- **Terms:** a **lane** is an owned scope of work; the **validation owner** independently re-runs its decisive check; **checkpoint/stop scope** defines when to pause and what ends iteration; a **bounded stop budget** caps attempts before asking the human for direction.
- Run focused red/green checks during implementation; they do not replace the final integrated verify. Required checks and normal coverage remain mandatory; failures block delivery. Rerun only when relevant code, inputs or runtime invalidate evidence. For dotfiles mechanics and blocked-tool handling, use the `dotfiles-verify` skill and `docs/ORCHESTRATION.md` in the dotfiles repository.
- For service or model-generator changes, use the real consumer and credential path, capture a baseline, run twice, and check health, generated state/keys and idempotence. Isolate `HOME` and `PATH`, stub real service/process commands in tests, and confirm live services survived verification.
- Keep status factual (command, exit, HEAD and outcome); do not claim Markdown can repair host or adapter liveness.
- Before takeover, confirm termination and inspect actual partial state. Never overlap writers or clean destructively.

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

- Use bounded, independently owned lanes only when useful; inspect actual changed files and persisted state before reconciling a report.
- Record worktree ownership, path, branch, and creation, integration and teardown consent in the approved lane. Retire unused agent-owned clean trees only under that consent; preserve unique committed branches.
- Before removing dirty saved work, make a private verified archive with restore instructions and obtain explicit dirty-removal consent. Never remove primary, user-owned or unknown trees, touch user stashes, add verification stashes, or opportunistically prune/delete branches.
- Directory access is not integration or deletion consent. Use the existing worktree skill's nested `.slim/worktrees` default where applicable; dotfiles sibling locations require one explicit approved exception, not a new default.

### Third-party tool claims

Before building on third-party tool behaviour (env vars, config keys, CLI flags), verify locally with the tool's introspection (`--help`, `dump-config`, `config show`, or a scratch probe); documentation can be stale. Scope capability assumptions to the specific tool/version verified, not universal claims.

For GitHub rulesets API details, see `configs/agents/repo-agents-shared.md` in the dotfiles repository: `rules` and `bypass_actors` must be JSON arrays; omit `integration_id` from `required_status_checks`; set workflow `permissions` at workflow or job level, never step level.

### Planning scope

When a feature or change touches the AI tooling fleet, assess every tool configured in the repo upfront, not just the obvious ones. The configured fleet is documented in `docs/CAPABILITY_MATRIX.md`.

> Skills distribution is documented in the dotfiles repo's `AGENTS.md` and `docs/ORCHESTRATION.md`.

<!-- AGENT_GUIDANCE_END -->
