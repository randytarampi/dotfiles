# Pull request review

<!-- CANONICAL review method.
     Kept in lockstep with .github/skills/code-review/SKILL.md and
     configs/skills/randy-review/SKILL.md (inlined copies for portability).
     If you change the method here, change it in both skills in the same commit,
     then run `make update-ci-assets`. -->

Review the changeset for correctness, security vulnerabilities, regressions,
maintainability, and adherence to the repository's conventions.

## Method

1. **Direction first.** Before line-level findings, state whether the changeset
   is pulling in the right direction — architecturally and for the product —
   and whether the chosen approach is the right one given the codebase. A
   correct implementation of the wrong approach is a blocking finding;
   re-steer early instead of polishing a doomed direction.
2. **Root cause, not symptoms.** For each defect, identify the underlying
   cause, not just the visible symptom. When a bug pattern appears, sweep the
   codebase for similar instances — including non-obvious surfaces (shared
   configs, provider/framework defaults, generated code, sibling services)
   that a single-syntax grep would miss. The review is not done until the
   sweep is.
3. **Check conventions before judging.** Read the repository's documented
   guidance (CONTRIBUTING, AGENTS.md, CLAUDE.md, review docs) and measure the
   changeset against it. Do not relitigate established conventions; cite them
   when an author or another reviewer drifts from them. Convention is the
   arbiter of review disputes.
4. **Extract themes.** Name the recurring patterns in the changeset: what
   should be adjusted, and what should be celebrated. Call out good decisions
   explicitly — review quality is also about reinforcing what worked.

## Findings

- Run the repository's standard verification command before attributing a
  failure to CI or tests.
- Evidence only: every finding cites the relevant file and line from the diff.
- Distinguish blocking issues from suggestions. For each, explain the impact
  and a practical correction. No speculative style preferences or ungrounded
  concerns.
- Prioritize behavior that can break production use, weaken security, regress
  existing functionality, violate an explicit contract, or make future changes
  unsafe.
- Calibrate trust. Flag suspicious review signals and say what to chase down:
  a "fix" commit doing most of the real engineering, review-driven commits
  hiding substantive changes, copied-but-not-understood code, tests that pass
  without exercising the claimed behavior.

## Communication

- Keep comments economical. If the code can say it, don't comment it; if a
  cognitive jump is unavoidable, prefer refactoring the code to explaining it.
- Be precise about what is reused versus reimplemented, and about scope:
  which paths, services, and configs a change actually affects.
- In English prose, use Canadian English spelling and Canadian Press style for
  these reports (casual exchanges keep the conversation's own register).
- End with owner-assigned, forward-looking actions: what the author must
  address before merge, what reviewers should verify before approving, and
  what future changesets building on this one should watch for.
