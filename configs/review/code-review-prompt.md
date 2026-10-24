# Pull request review

<!-- Canonical rubric. Portable fallback copies are kept in lockstep in the two review skills. Update configs/review/assets-manifest.json when this changes. -->

Review this changeset against its stated outcome, affected contracts and the repository's own conventions. Prefer correctness, safety and evidence over stylistic preferences.

## Method

1. **Scope and direction.** Identify the intended outcome, affected consumers and proof required. Challenge a direction only when it creates a concrete material risk. A different architectural preference is not by itself a blocker.
2. **Prove the defect.** Inspect the changed code and actual producer/consumer boundaries. Cite the file, diff line, failure mechanism and reproducible evidence or a precise testable scenario. Do not infer correctness from green CI or failure from a bot's confidence score.
3. **Bound root-cause checks.** Inspect directly affected siblings, shared interfaces, generated consumers and security boundaries, including unchanged dependent files. A failing shared contract may require a bounded investigation across components: follow the producer/consumer chain far enough to establish the full defect. Fix every part required by this PR's correctness or security contract; record independent improvements as separate follow-ups.
4. **Respect local policy, question it with evidence.** Read the invoked repository's guidance. It is the default, not an excuse to perpetuate a broken contract. State an evidence-backed exception or propose a small policy change.
5. **Check the final state.** Verify each PR on its actual head with targeted regression evidence and applicable checks. For a dependent PR stack, also verify the integrated stack tip and the final mainline state after parent-first landing; individual branch checks do not prove end-to-end acceptance. Label machine-specific checks owner-reported, unverified or not applicable; never invent results.

## Findings and disposition

- **Block:** demonstrated correctness, data-integrity, security, compatibility or contract failures; misleading pass/fail signals; untested destructive behaviour; and a maintainability defect only when its concrete risk makes the change unsafe to accept.
- **Advise:** speculative risks, optional abstractions, complexity/duplication metrics without a demonstrated defect, broader cleanup and unrelated pre-existing debt. Automated linter failures are deterministic local checks, not open-ended review debates.
- For each finding provide: severity, affected file/line (diff or unchanged dependent code), observable impact, minimal correction, and what would prove resolution. Avoid duplicate comments when another reviewer already gave the same actionable finding.
- Track each material finding as **fixed** (with evidence), **declined** (reason/owner acceptance), **deferred** (explicit follow-up, scope and nonblocking rationale), or **open**. **A demonstrated required correctness, security or data-integrity failure cannot be waived by a routine decline or deferral.** It stays blocking until corrected or an explicit change to the governing contract is separately approved and verified. Re-review only new changes and unresolved material risks; do not restart the whole review on every fix commit.
- Distinguish **PASS**, **FAIL**, **UNKNOWN** and **SKIPPED**. An unavailable catalogue, missing credential, skipped test or empty review body is never evidence of success. For mutators, validate before writing, preserve unmanaged state and fail closed on unsafe partial application.
- A review is complete when the agreed acceptance contract is met, required checks succeed, every material finding has an explicit justified disposition, and no required-contract failure remains unresolved—not when all possible improvements have been implemented. Prefer one bounded correction pass and one confirmation review; additional iterations require new material evidence.

## Report

Start with a short direction and merge-readiness verdict. State blocking findings before suggestions. End with the smallest owner-assigned next actions, unresolved risks and exact checks observed. Keep comments concise. Use Canadian English in formal reports when the invoked repository requests it.
