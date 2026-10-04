---
name: dotfiles-verify
description: >-
  Verify dotfiles repository changes before reporting completion. Use when
  running make verify, checking Python formatting, or validating work before
  saying it is done. Triggers include verify, make verify, black, format,
  pycache, check-cli-contract, done, finished, and before commit.
---

# Dotfiles Verify

Before claiming work is done in the dotfiles repo, run the applicable checks below and report their actual results. A missing required tool or dependency is **BLOCKED**, not a pass or a reason to silently skip a required check.

## 1. Format Python files

For Python changes, format/check only the touched Python files. Use the installed `black` executable; do not reformat unrelated files.

```sh
black --check path/to/touched.py [other/touched.py ...]
```

If any files need reformatting:

```sh
black path/to/touched.py [other/touched.py ...]
```

## 2. Run the repo's standard verify command

```sh
make verify
```

For code or configuration changes, run the complete `make verify` once on the stable intended committed state, using a clean dedicated verify worktree or committed `HEAD` checks. Unrelated dirty working-tree content is not proof; never stash or clean another lane's work to make verification pass. It must exit 0 at the repository's normal test-coverage threshold. Focused red/green checks during implementation and independent targeted lane checks do not replace this final integrated check. Rerun it when relevant code, inputs or runtime invalidate the evidence, not for unrelated edits. Do not lower the coverage floor to make a targeted run pass. A targeted pytest selection should use `--no-cov` so it does not compare partial coverage with the global threshold. If required tooling or declared test dependencies are unavailable, stop and report BLOCKED; do not omit that component or call the result verified. For documentation-only or trivially mechanical edits, use relevant documentation checks and state clearly that full verification was not run.

## 3. Add targeted checks only when useful

`make verify` already runs the CLI-contract check. Run it separately only when its inputs changed and a focused diagnosis is needed; otherwise avoid duplicating it. For focused Python tests, use the repository's Poetry environment and `--no-cov` when measuring only a subset.

## Notes

- Do not unconditionally remove `__pycache__` or other ignored artifacts; they are not source changes or verification failures.
- If `make verify` fails, fix the issue before reporting success. Do not pipe it through output truncation and then report the pipeline's status as the verify status. Prefer direct invocation; if a wrapper must capture output, inspect `make verify`'s own exit code and retain the diagnostic output.
- If Black or another required tool is missing, report BLOCKED and arrange the documented environment before continuing; never silently skip formatting.
- For service/model-generator changes, use the real consumer and credential path and capture a baseline before the first run. Run twice; check health, generated state including keys, and idempotence. Service/orchestration tests isolate `HOME` and `PATH` and stub process/service commands. Confirm live services survived verification; verification may mutate runtime state.
