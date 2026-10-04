## Repository Guidance

These policies apply to work in every repository.

### Verification

- **Lifecycle.** This restates the home "Verifying before declaring success" section as hard rules: one lane acts at a time, each proves its thin acceptance slice, then a single canonical integrated verification runs on the final intended state before anything ships, and finishing includes retiring your worktree. Where both files are present they describe one gate — keep them in sync.
- Agree on outcome, acceptance checks, delivery state, permissions, and validation owner at lane start. Use focused red/green checks while implementing; the validation owner independently inspects each lane and runs its decisive targeted reproduction. Run one canonical integrated verification on the stable intended state before completion, push or release. Re-run when relevant code, inputs or runtime invalidate evidence. Required checks and normal coverage remain mandatory; failures block delivery.
- Report execution only from actual tool results and persisted state where applicable; never simulate calls, results or transcripts. Resolve contradictory claims from actual Git/remote HEAD, index and CI state. Keep status factual and concise; PID-alive/busy is not progress evidence.
- For each lane, record objective, role, absolute path, base SHA, owned files, validation owner, checkpoint and stop conditions. Reuse only for the same objective and role. Use the existing runtime preflight, test the thinnest observable acceptance slice first, and stop to reassess if infrastructure dwarfs behaviour. Agree on a checkpoint and bounded stop budget; change approach after two failed attempts on the same subsystem; if it is still unresolved, stop and return to the human for a decision — do not accumulate more than three fix commits on one subsystem without an explicit scope check. Take over only after confirming termination and inspecting partial state. Never overlap writers or clean destructively.
- A service environment schema change is not complete in one surface: when adding, removing, or renaming a key in a generated `service.env`, update the generating `*_service_env_sync()` emitter, the verification script's required-names/expected set, and the env-sync test asserting the key schema — all in the same change.
- For service or model-generator changes, use the real consumer and credential path, capture baseline before the first run, run twice and check service health, generated state/keys and idempotence. Isolate `HOME` and `PATH`, stub real service/process commands in tests, and confirm live services survived verification. Dotfiles command detail is in the `dotfiles-verify` skill.
- Do not sleep-poll CI or specialist lanes; dispatch feedback-addressing work while checks run in the background and reconcile results when they land.
- At implementation-lane start, confirm the base/worktree state, available runtime and declared dependencies; agree on a red-before/green-after acceptance check, its validation owner, and a checkpoint/stop scope. Do not begin edits when a required runtime is unavailable.
- If a check blocks, inspect persisted files and process/service state first, attempt bounded recovery inside the agreed scope, then report the blocker to the validation owner. Do not silently weaken acceptance criteria or expand into adjacent work.
- Stage explicit file paths, never `git add -A`: tooling temp files (coverage fragments, caches) land in untracked state and get swept into wholesale staging.
- Prefer REST (`gh api repos/OWNER/REPO/...`) for label/metadata operations; `gh pr` GraphQL subcommands may fail on tokens without `read:org`.

### Commits and pushes

- Keep one concern per commit.
- Use Conventional Commits (`type(scope): description`).
- Never push unless the user explicitly authorizes it.
- Before any force-with-lease, finish the rebase and verify both a clean Git status and the expected tip. Never push mid-conflict.
- All PRs created from agent work — fixes, backlog clean-ups, dependency bumps, generated changes — are opened as draft (`gh pr create --draft`) and stay draft. Never mark your own PR ready for review; a human (you, or a colleague in the downstream pass) turns the draft into a reviewable PR when they're satisfied.
- Don't auto-close a backlog issue with `Closes #N` when the PR fixes only a subset; track unresolved review threads individually.

### Git worktrees

- Use worktrees for meaningful isolation or parallelism, not every small edit. One writer owns one branch and worktree; never attach a branch to multiple worktrees or dispatch into an owned lane. Preserve pre-existing dirty state; never stash, clean, reset or broadly stage another lane's work. Stage explicit paths only.
- In the approved lane, record agent ownership, path, branch, and creation/integration/teardown consent. Finishing includes retirement: remove unused agent-owned clean trees under that standing consent. Preserve unique committed branches. Before removing dirty saved work, create a private verified archive with restore instructions and obtain explicit dirty-removal consent. Never remove primary, user-owned or unknown trees; touch no user stashes, add no verification stash, and do not opportunistically prune or delete branches. Directory access is not integration/deletion consent. Follow the existing worktree skill's nested `.slim/worktrees` default where applicable; dotfiles sibling worktrees need one explicit approved exception, not an implicit default. See `docs/ORCHESTRATION.md` for dotfiles preflight/location details.

### Writing and ambiguity

- Use Canadian English in prose and Canadian Press style for formal artifacts.
- Ask before implementing when a flag or name has ambiguous semantics; do not guess when the cost of being wrong is high.

### Delegation and planning

- For unknown scope, delegate bounded discovery first; read expected edit targets directly. At the outset agree the outcome and a few acceptance checks, delivery state, and permission boundaries. Classify new asks as blocking now, next batch or future, honouring explicit user overrides. Finalisation freezes scope to correctness and required CI until delivery.
- When changing AI tooling, assess every configured tool up front and enumerate the full tool fleet.
- Keep repository-specific facts and implementation details in the repository's own guidance and documentation.
- Never dispatch onto a repo another lane may own. Verify actual tip, dirty state and process/tool evidence before re-dispatch or takeover; ambiguous status is not proof a session ended. Respect the repository's pinned runtime. Scope capability assumptions (including image support or specialist skill access) to the specific tool/version verified.

### API verification notes

- Verified dotfiles rulesets API facts: `rules` and `bypass_actors` must be JSON arrays; omit `integration_id` from `required_status_checks`; set workflow `permissions` at workflow or job level, never step level. Re-introspect current tool/API behaviour before relying on it.

### Communication

- Distinguish required CI, fresh current-head review, review comments and owner-accepted trade-offs. Record findings as fixed, declined by owner, or deferred. Resolve stale requested changes or absent bot reviews at one bounded owner decision point, not endless loops. Agent PRs remain draft by default; never fake approval, bypass rulesets, or push/mark ready/merge without explicit user authority. An explicit override is recorded, not represented as bot approval.
- Image/screenshot support depends on the specific agent and version; verify the capability before relying on it. Ask for a text description or probe an artifact when the lane cannot read it.

### Artifacts

- Probe binary artifacts with appropriate local tools before concluding they are unreadable; never ask the user to resend an unreadable artifact.
