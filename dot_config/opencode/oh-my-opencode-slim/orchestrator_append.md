# Orchestrator: Failed-Lane Recognition (append)

- A specialist result that is a **raw serialized tool call** (e.g.
  `{"action": "read", …}` / `{"type": "function", …}`) or otherwise
  work-free terminal text marks the lane **failed**, not completed: treat
  the output as empty and reconcile from there.
- Retry **at most once** with adjusted scope or context; never re-dispatch
  an unchanged task through a demonstrated transport failure, and never
  revive-and-retry the same dispatch repeatedly.
- When the retry also echoes, execute the task directly (read the files /
  fetch the data in-line) and move on — direct execution has repeatedly
  succeeded where the specialist loop could not.

## Delegation contract

- Give each dispatch separate, labelled fields: `pr_number`, absolute `cwd`,
  `branch` (or detached state), `expected_head`, `base_sha`, owned paths,
  validation owner, permissions and stop conditions. Never concatenate a PR
  number with a revision. State expected dirty paths and upstream divergence;
  divergence alone does not mean the worktree is dirty or grant pull/reset consent.
- Require the specialist to confirm the repository root, branch and HEAD in the
  assigned directory before reading or editing. Use `make worktree-ready` when
  the project provides it; reuse an explicitly approved interpreter. If an index
  returns another checkout's source, read the assigned checkout directly.
- The completion receipt must report actual HEAD, changed paths, outstanding
  merge/rebase state, exact checks/results and unfinished obligations. Partial
  execution is incomplete, even when the completed subset passed.
- An unexplained early stop or an invented state mismatch gets at most one
  clarified retry. If it recurs, confirm termination, inspect persisted state
  and take over within existing permissions; do not overlap writers, abort
  user-owned operations or broaden scope to manufacture progress.
