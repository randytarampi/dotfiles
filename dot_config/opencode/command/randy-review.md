---
description: Review changes using the shared Randy review method
---

Review the current changes using the shared review method from the
`randy-review` skill (read that skill first; it carries the full method with a
locator chain and inlined fallback — do not substitute your own rubric).

## Execution

1. Determine the review target (uncommitted diff, commit, branch, or PR) and
   gather the full file context for every changed file — diffs alone are not
   enough.
2. Run the repository's standard verification command before attributing any
   failure to CI or tests.
3. Apply the method: direction verdict first, then findings (each citing
   file:line, classified blocking vs. suggestion), then recurring themes —
   what to adjust and what to celebrate — then owner-assigned next actions.
