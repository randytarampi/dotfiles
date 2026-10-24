---
name: randy-review
description: "Randy's shared review method for any repository. Use when the user asks for a code review, PR review, or changeset review. Triggers on \"review\", \"code review\", \"PR review\", \"randy-review\"."
---

# Randy review method

<!-- PORTABILITY COPY of configs/review/code-review-prompt.md (canonical).
     Kept in lockstep with .github/skills/code-review/SKILL.md. If the method
     changes there, change it here in the same commit. -->

Execute this method — do not substitute your own rubric.

## Locate the canonical method

Prefer, in order:

1. A trusted checkout at `__TRUSTED_DOTFILES__/configs/review/code-review-prompt.md`
   (present in CI runs of the agentic-review workflow).
2. `configs/review/code-review-prompt.md` in the current repository.
3. The inlined method below (use this whenever neither file exists; you do not
   need to announce the substitution).

## Context rules

- Review **the repository you are invoked in** — its patterns, customs, and
  conventions, not another repository's. Read its documented guidance
  (CONTRIBUTING, AGENTS.md, CLAUDE.md, REVIEW.md), use its standard
  verification command, and measure the changeset against those.
- Never carry conventions in from the repository that distributes this skill.
  If you cannot find a convention in the invoked-in repository, do not
  assume one; ask or note the uncertainty explicitly.

## Inlined method (fallback)

Review this changeset against its stated outcome, affected contracts and the repository's own conventions. Prefer correctness, safety and evidence over stylistic preferences.

## Method

1. **Scope and direction.** Identify the intended outcome, affected consumers and proof required. Challenge a direction only when it creates a concrete material risk. A different architectural preference is not by itself a blocker.
2. **Prove the defect.** Inspect the changed code and actual producer/consumer boundaries. Cite the file, diff line, failure mechanism and reproducible evidence or a precise testable scenario. Do not infer correctness from green CI or failure from a bot's confidence score.
3. **Bound root-cause checks.** Check directly affected siblings, shared interfaces, generated consumers and security boundaries. Fix related defects required by this PR's contract. Record independent findings as separate follow-ups rather than extending the PR indefinitely.
4. **Respect local policy, question it with evidence.** Read the invoked repository's guidance. It is the default, not an excuse to perpetuate a broken contract. State an evidence-backed exception or propose a small policy change.
5. **Check the final state.** Verify on the actual head, with targeted regression evidence and the repository's applicable checks. Mark machine-specific checks as owner-reported, unverified or not applicable; never invent a test result.

## Findings and disposition

- **Block:** demonstrated correctness, data-integrity, security, compatibility or contract failures; misleading pass/fail signals; untested destructive behaviour; and a maintainability defect only when its concrete risk makes the change unsafe to accept.
- **Advise:** speculative risks, optional abstractions, complexity/duplication metrics without a demonstrated defect, broader cleanup and unrelated pre-existing debt. Automated linter failures are deterministic local checks, not open-ended review debates.
- For each finding provide: severity, file/line, observable impact, minimal correction, and what would prove resolution. Avoid duplicate comments when another reviewer already gave the same actionable finding.
- Track each material finding as **fixed** (with evidence), **declined** (reason/owner acceptance), **deferred** (explicit follow-up and scope), or **open**. Re-review only new changes and unresolved material risks; do not restart the whole review on every fix commit.
- Distinguish **PASS**, **FAIL**, **UNKNOWN** and **SKIPPED**. An unavailable catalogue, missing credential, skipped test or empty review body is never evidence of success. For mutators, validate before writing, preserve unmanaged state and fail closed on unsafe partial application.
- A review is complete when the agreed acceptance contract is met, required checks succeed, and every material finding has an explicit disposition—not when all possible improvements have been implemented.

## Report

Start with a short direction and merge-readiness verdict. State blocking findings before suggestions. End with the smallest owner-assigned next actions, unresolved risks and exact checks observed. Keep comments concise. Use Canadian English in formal reports when the invoked repository requests it.
