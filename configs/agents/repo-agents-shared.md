## Repository Guidance

These policies apply to work in every repository.

### Verification

- Run the repository's canonical verification command before claiming success.
- If verification fails, fix it before reporting the work as complete.
- Verify from the committed tree, never in-flight working-tree content: use a clean dedicated worktree or verify `git show HEAD:<file>` rather than stashing or cleaning another lane's changes.
- Treat a lane's verification claim as unproven until it is independently re-run: re-execute the repo's verify command (and `actionlint` on workflow changes) before accepting it.
- A service environment schema change is not complete in one surface: when adding, removing, or renaming a key in a generated `service.env`, update the generating `*_service_env_sync()` emitter, the verification script's required-names/expected set, and the env-sync test asserting the key schema — all in the same change.
- For service or model-generator changes, run the generator twice; check service health and generated state, including keys, and confirm the second run is idempotent. A successful first run alone can hide failures such as an HTTP 400 from an invalid key list.
- Tests for orchestration or service helpers must isolate `HOME` and `PATH` and stub real service/process commands such as `launchctl`, `systemctl` and `pkill`. After full verification, confirm live services survived; don't assume verification is non-mutating.
- Do not sleep-poll CI or specialist lanes; dispatch feedback-addressing work while checks run in the background and reconcile results when they land.
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

- Use a dedicated Git worktree for non-trivial, risky, or parallel work. Keep the primary checkout as an integration lane, especially when it has pre-existing dirt.
- One writer owns one branch and one worktree. Never attach the same branch to multiple worktrees or dispatch another writer into an owned lane.
- Before work starts, record the expected dirty state. Preserve it: do not use `git stash`, `git clean`, `git reset`, or broad staging to clear another lane's work.
- Stage explicit paths only. Validate from the intended committed worktree state, not from unrelated changes in the primary checkout.
- Before removing a worktree, confirm it has no uncommitted changes and ask for explicit approval. Do not prune unrelated or stale worktree records opportunistically.

### Writing and ambiguity

- Use Canadian English in prose and Canadian Press style for formal artifacts.
- Ask before implementing when a flag or name has ambiguous semantics; do not guess when the cost of being wrong is high.

### Delegation and planning

- For unknown scope, delegate bounded discovery first; read expected edit targets directly.
- When changing AI tooling, assess every configured tool up front and enumerate the full tool fleet.
- Keep repository-specific facts and implementation details in the repository's own guidance and documentation.
- Dispatch discipline: never dispatch onto a repo another lane may own. When a background signal contradicts the Job Board, or the board shows `error`/unknown for a session, verify the repository's tip and dirty state directly before re-dispatching — a stale or ambiguous board signal is not proof a session is gone.
- Brief lanes with the repository's expected dirty state at dispatch time (pre-existing modifications to preserve, intentional uncommitted files), so preflight stops are reserved for genuine drift.
- Run lanes under the repository's pinned Node version (check `.nvmrc`); never the machine default.
- Never run long `sleep`/poll loops in the orchestrator shell; dispatch a read-only watcher lane and end the turn.
- Watcher lanes run to terminal state and report conclusions; don't return on a first in-progress poll.
- Specialist sessions cannot load skills — inline the relevant skill's workflow in the dispatch prompt.

### API verification notes

- Verified live 2026-09-05; recheck these facts before debugging around them.
- GitHub Actions `startup_failure` runs expose no check-run, job or annotation API artifacts; use the Actions UI.
- GitHub environment REST responses may omit required reviewers; trust a release run's `waiting` state or the Settings UI, and verify GraphQL types against the schema.
- GitHub Actions allowlists match the full `owner/repo/path@ref`; audit every `uses:` entry, including subpaths and aliases.
- AppVeyor build-job logs are raw text, not JSON.
- Coveralls badges can be stale; use project build JSON for current coverage.
- Unpublished npm versions cannot be republished; release a higher version.

### Communication

- Image and screenshot inputs are not supported in agent lanes; ask for text, a description, or a probed artifact (`pdftotext`, `xxd`) instead of accepting an unreadable file.

### Artifacts

- Probe binary artifacts with appropriate local tools before concluding they are unreadable; never ask the user to resend an unreadable artifact.
