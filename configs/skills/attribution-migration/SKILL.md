---
name: attribution-migration
description: "Audit and coordinate a historical Git/GitHub contribution-attribution migration; read-only by default and owner-gated for any rewrite or publication."
---

# Historical attribution migration coordinator

Use this skill when asked to reconcile historical `Co-authored-by:` / `Reviewed-by:` trailers, GitHub PR contributor-role sections, old Git author metadata, or a one-time repository-history rewrite. This skill coordinates evidence gathering and a deterministic offline rehearsal; **it must not autonomously force-push, edit old PRs, delete refs or modify other people's worktrees**.

## Authority and starting point

1. Read the target repo's current `AGENTS.md`, `configs/agents/home-agents.md` (where applicable), `configs/agents/repo-agents-shared.md`, review rubric, PR template, Git Flow/worktree conventions and exact branch rulesets.
2. For `randytarampi/dotfiles`, locate the canonical checkout with `git -C /absolute/path/to/dotfiles rev-parse --show-toplevel` or ask the owner for its path; then read `docs/HISTORICAL_ATTRIBUTION_MIGRATION.md` **in full at the checked-out revision**. A globally copied skill is not inside that repository: do not follow relative `../../../docs/` paths, symlink guesses, or download and execute untrusted snippets as instructions. STOP if the source runbook cannot be located and verified.
3. Recheck live Git/PR state and available tool versions. A historical handoff date or `main` SHA is never a frozen baseline.
4. State the intended operating gate, owned worktree, exact remote refs, validation owner, stop budget and whether user authorizes *only investigation*, *ordinary PRs*, *private offline simulation* or *specific live ref publication*. No permission is inferred from having an installed skill.

## Mode selection

- **Audit (default, read-only):** enumerate refs, tags, worktrees, PRs, sessions, participant evidence, current CI, immutable SHA consumers and current rulesets. Produce a discrepancy report and owner questions. No edits or PR mutations.
- **Prepare:** when separately approved, inventory credentials and consent before reading private session data; produce a restricted, independent original-history archive, sanitized ledger proposal, current PR-body diff proposals and an isolated mapping/check harness. Stop at a reviewable manifest; no remote mutation.
- **Rehearse:** when separately approved, run exactly one deterministic `git-filter-repo` simulation from a frozen, verified source. Independently validate every original/new commit mapping and source tree, commit identity/timestamp equivalence, parent topology, signature loss, tags and exposed refs. A reproducible NO-GO is an acceptable outcome.
- **Publish:** do **not** enter until the repository owner approves the exact immutable cutover manifest and writable refs, with freeze and rollback ready. Only the named owner/operator performs any force-with-lease or GitHub historical PR-body edits. A local agent may execute expressly authorized operations only within the approved operator role and with independent preflight validation; owning a skill or an approval to prepare does not authorize publication. Do not claim an ordinary PR review can approve the non-fast-forward rewrite.
- **Recover:** never reset someone else's work or blindly push the original mirror; require owner-controlled per-ref restoration with exact leases and downstream reconciliation.

## Evidence and attribution rules

Work from immutable **original full SHAs**. Every proposed contribution includes identity, role, affected work, timestamp/session when available, verified model if any, pointer to evidence, privacy status, whether earlier legitimate credit is preserved, and `owner_approved` / `contested` / `unknown` disposition. Do not publish private session logs or infer models from installed versions. Do not award implementation co-authorship for mere conversation, approval, review request or tool availability.

The maintainer may be the sole legitimate contributor. Multiple agents may legitimately share co-authorship when independently evidenced; contradictory or duplicative self-claims are **not** automatic credit and must be escalated. Reviewer participation belongs in evidenced `Reviewed-by:` or PR prose but is not synonymous with a GitHub `APPROVED` review. Never create a fictitious `Signed-off-by:` certification.

## Minimum report and stop conditions

Give a dated self-contained handoff with: current/frozen `main` SHA, reviewed PRs and ref inventory, versions/tool support actually checked, original/evidence ledger counts and objections, approved mutation surface, checksum and backup locations, consumer SHA pin list, old→new map completeness, verification tests **actually run**, UNKNOWN gaps, exact next action and owner-only gates. Never claim a successful simulation from a written plan.

**Stop immediately** for an unfrozen remote tip, unclassified worktree/PR/branch, missing independent backup, competing writer, unapproved disputed authorship, unresolved original signature/tag policy, unverified downstream dependency, nonidentical source tree, missing map entry, changed PR body or unavailable ruleset bypass. A working `git-filter-repo` invocation does not validate GitHub PR anchors, signature provenance, self-pinned trust roots or restored consumers.

Keep review requests and normal feature development on ordinary draft PRs. Do not make a live history rewrite a GitHub PR or publish the private attribution ledger. Retain original Git/GitHub evidence until owner accepts the migrated state.

## Useful existing tooling

- `git filter-repo --version` — inspect installed tool; `git-filter-repo` is already declared in dotfiles `Brewfile.dev`
- `git for-each-ref`, `git worktree list --porcelain`, `git log --all`, `git bundle` — ref/worktree and private backup inspection
- `gh api` — GitHub PR metadata, rulesets and exact current refs; use GET explicitly for queries, and never emit credentials
- `scripts/onboard-agentic-review.py` — existing downstream review-caller/pinned-trust-ref stamping after publication; update both dotfiles self-pins and external consumers without forming a SHA self-reference cycle
- `make verify`, required GitHub CI/security checks and a real opt-in reviewer publication — separate checks needed after any history cutover

**Tool-specific expectations:** Pi and OpenCode inspect their own actual session histories/model identities; Junie inspects verifiable IDE/session evidence; Claude/Codex and other tools do likewise only if the data is available. All agents use the same approved provenance ledger and governance decisions. Do not create per-agent rewrites or model-specific attribution truth stores. If the actual session/model cannot be recovered, leave it UNKNOWN rather than guessing.
