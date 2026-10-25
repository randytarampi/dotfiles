## Repository Guidance

These policies apply to work in every repository.

### Verification

- **Delivery gate.** Same delivery gate and order as the home lifecycle; repo-specific hard rules follow.
- The validation owner independently inspects each lane and re-runs its decisive targeted check (`actionlint` for workflow changes).
- For code or configuration changes, run one canonical integrated verification before completion, push or release. In dotfiles, use `make verify`; elsewhere, use the repository's documented command.
- Verify the stable intended committed state, using a clean verify worktree or committed `HEAD` checks. Unrelated dirty working-tree content is not proof; if the agreed delivery is intentionally local and uncommitted, label it and verify that exact tree.
- For documentation-only or trivially mechanical changes, run relevant documentation checks and state that full integrated verification was not run.
- Re-run checks only when relevant code, inputs or runtime invalidate the evidence. Required CI remains mandatory; normal coverage remains mandatory when integrated verification applies. Failures block delivery.
- Report execution only from actual tool results and persisted state; never simulate calls, results or transcripts. Actual Git/remote `HEAD`, index and CI state prevail over conflicting reports; status or a live PID is not progress proof.
- Record each lane's objective, role, absolute path, base SHA, owned files, validation owner, checkpoint and stop conditions. Reuse it only for the same objective and role.
- Use the existing runtime preflight and prove the thinnest observable acceptance slice first. If infrastructure dwarfs behaviour, stop and reassess the test seam.
- Agree on a checkpoint and bounded stop budget. Change approach after two failed attempts on one subsystem; if it remains unresolved, stop and ask the human. Do not start a fourth fix commit without an explicit scope check.
- Before takeover, confirm termination and inspect partial state. Never overlap writers or clean destructively.
- A generated `service.env` schema change spans three surfaces: update the `*_service_env_sync()` emitter, verification script's required/expected names, and the env-sync test in the same change.
- For service or model-generator changes, use the real consumer and credential path, capture a baseline, and run twice to check service health, generated state/keys and idempotence.
- Service/orchestration tests isolate `HOME` and `PATH` and stub real service/process commands. Confirm live services survived verification; it may mutate runtime state. Dotfiles command detail is in the `dotfiles-verify` skill.
- Do not sleep-poll CI or specialist lanes; dispatch feedback-addressing work while checks run in the background and reconcile results when they land.
- At implementation-lane start, confirm base/worktree state, available runtime and declared dependencies. Agree on a red-before/green-after acceptance check, its validation owner and checkpoint/stop scope. Do not begin edits when a required runtime is unavailable.
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

### Contribution attribution

Credit substantive work by humans **and** AI agents at the point of delivery.
Do not erase earlier contributors when handing work to another model or agent.

- **Commits:** retain the actual Git author. For each additional person or agent
  who materially designed, wrote or revised the committed change, add a
  `Co-authored-by: Name <email>` trailer when a verified or approved attribution
  identity is available; optionally include `(model)` after the name only
  when the session reliably identifies its actual model. For ChatGPT use
  `Co-authored-by: ChatGPT <noreply@openai.com>` by default; a verified
  session may use `ChatGPT (gpt-6)` or its actual model. OpenCode agents use a base
  identity of `Co-authored-by: opencode <opencode-agent[bot]@users.noreply.github.com>` with the model
  derived dynamically at commit time (e.g. `Co-authored-by: opencode (poolside/laguna-xs-2.1) <opencode-agent[bot]@users.noreply.github.com>`).
  Junie sessions use `Co-authored-by: Junie <junie@jetbrains.com>`; include the
  runtime model in parentheses only when the session actually reports it
  (e.g. `Co-authored-by: Junie (grok-4.6) <junie@jetbrains.com>`).
  For Pi sessions here, use `Co-authored-by: Pi <noreply@pi.dev>`; include the
  runtime model in parentheses only when the session reports it
  (e.g. `Co-authored-by: Pi (omlx/Ornith-1.5-35B-A3B-MLX-4bit) <noreply@pi.dev>`).
  Do not guess a family or version. Use the contributor's own supported name/model/email for
  other agents; never invent an email or impersonate a human or GitHub user. If
  no suitable identity is available, acknowledge the agent and contribution in
  the PR or document instead.
- **Reviewed-by trailers:** when a PR or code review is attributed to an agent,
  use `Reviewed-by: Name (model) <email>` with the same conventions. OpenCode agents
  derive their model at time of review; if unavailable, use the base identity
  `Reviewed-by: opencode <opencode-agent[bot]@users.noreply.github.com>`. For example,
  `Reviewed-by: Claude (anthropic/claude-opus-5-5) <claude@anthropic.com>`.
  When an agent session hands off to another agent, both should be acknowledged
  if they each contributed material review input.
- **Pull requests:** include a short **Contributors and roles** section naming
  significant human and agent implementers, drafters and reviewers, with the
  specific work each did. Co-author trailers do not replace this record. Credit
  reviewers for reviews; do not automatically call them code co-authors.
- **Design docs, RFCs and handoffs:** identify the actual author(s),
  collaborating agents, reviewers, date and their roles in a small attribution
  note. Preserve existing provenance when revising a document. Do not add
  boilerplate to every incidental note or generated file; credit substantial
  contributions in the parent artifact or PR.
- **Truth and consent:** attribute only work actually contributed, never claim
  approvals, authorship, tool execution or tests not performed, and respect a
  contributor's requested identity/privacy preference. If authorship is
  uncertain, say so rather than guessing.
- **History:** apply this prospectively. Do not rewrite existing/shared commit
  history merely to add credit without explicit authorization and coordination;
  an accurate PR acknowledgment is usually enough.
- Put all `Co-authored-by` and `Reviewed-by` trailers in one contiguous block at
  the end of the commit message, after one blank line from the body, with no
  blank lines between trailers. Remove exact duplicate trailer entries while
  preserving distinct contributors, models and roles; never claim unapproved
  approval. Keep the actual Git author unchanged.

### Git worktrees

- Use worktrees for meaningful isolation or parallelism, not every small edit. One writer owns one branch and worktree; never attach a branch twice or dispatch into an owned lane. Parallel lanes require disjoint, recorded ownership.
- Preserve pre-existing dirty state: never stash, clean or reset another lane's work. Stage explicit paths only; never broadly stage another lane's changes.
- In the approved lane, record agent ownership, path, branch, and creation, integration and teardown consent.
- Retire unused agent-owned clean trees only under recorded teardown consent. Preserve unique committed branches.
- Before removing dirty saved work, create a private verified archive with restore instructions and obtain explicit dirty-removal consent.
- Never remove primary, user-owned or unknown trees; touch no user stashes, add no verification stash, and do not opportunistically prune worktrees or delete branches.
- Directory access is not integration or deletion consent. Follow the existing worktree skill's nested `.slim/worktrees` default; dotfiles sibling worktrees need one explicit approved exception. See `docs/ORCHESTRATION.md` in the dotfiles repository for preflight/location details.

### Writing and ambiguity

- Use Canadian English in prose and Canadian Press style for formal artifacts.
- Ask before implementing when a flag or name has ambiguous semantics; do not guess when the cost of being wrong is high.

### Delegation and planning

- Honour the home lifecycle: agree outcome, acceptance checks, delivery state and permission boundaries up front; freeze scope at finalisation except for correctness and required CI.
- For unknown scope, delegate bounded discovery first; read expected edit targets directly.
- When changing AI tooling, identify the changed interface and inspect its actual consumers, including generated configurations. Broad fleet audits are warranted only for shared contracts, not unrelated tool integrations.
- Keep repository-specific facts and implementation details in the repository's own guidance and documentation. Prefer narrow, independently reviewable PRs; record unrelated findings as follow-ups instead of widening an in-flight changeset.
- Never dispatch onto a repository another lane may own. Before re-dispatch or takeover, verify its actual tip, dirty state and process/tool evidence; ambiguous status does not prove a session ended.
- Respect the repository's pinned runtime. Scope capability assumptions (including image support or specialist skill access) to the specific tool/version verified.

### API verification notes

- Verified dotfiles rulesets API facts: `rules` and `bypass_actors` must be JSON arrays; omit `integration_id` from `required_status_checks` only when checks from any source are acceptable; otherwise, set it to the trusted integration ID (do not send `null`). Set workflow `permissions` at workflow or job level, never step level. Re-introspect current tool/API behaviour before relying on it.

### Communication

- Distinguish required CI, fresh current-head review, review comments and owner-accepted trade-offs. Record material findings as fixed (with evidence), declined by owner, deferred with scope, or open. Resolve stale requested changes or absent bot reviews at one bounded owner decision point, not endless loops. Agent PRs remain draft by default; never fake approval, bypass rulesets, or push/mark ready/merge without explicit user authority. An explicit override is recorded, not represented as bot approval.
- Image/screenshot support depends on the specific agent and version; verify the capability before relying on it. Ask for a text description or probe an artifact when the lane cannot read it.

### Artifacts

- Probe binary artifacts with appropriate local tools before concluding they are unreadable; never ask the user to resend an unreadable artifact.
