# Historical contribution attribution: controlled Git history migration

**Status:** preparation only; this document grants no force-push, PR-edit or clean-up permission.
**Baseline inspected:** 3 November 2026, `main` at `8f69bb95615129d795011d179a75a339dce365c6`.
**Scope:** `randytarampi/dotfiles` plus approved downstream SHA consumers; re-enumerate everything at execution time.

This runbook is for a *local migration operator* (Pi, OpenCode, Claude Code, Codex, Junie or a human) taking over after normal development has stopped. The selected programme is **A + E + C**: reconcile attribution in GitHub PR bodies (A); make one controlled rewrite of explicitly approved Git refs (E); reconcile divergent / still-open branches not already covered (C). Source code behaviour must not change. This is not permission to create fictitious contributors or retroactive GitHub approvals.

Read `AGENTS.md`, `configs/agents/home-agents.md`, `configs/agents/repo-agents-shared.md`, `configs/review/code-review-prompt.md`, `.github/PULL_REQUEST_TEMPLATE.md`, `docs/ORCHESTRATION.md` and `docs/AGENTIC-REVIEW.md` from the **actual frozen revision**, not this snapshot. Preserve existing author/committer identity and previous legitimate attribution. Use conventional commits, one concern per commit, worktree ownership, draft PRs, independent validation and the repo's stop budget. This runbook's historical rewrite is a special operation requiring **separate exact-ref publication authorization** even if the plan has been approved.

## Essential decisions and boundaries

1. **Provenance beats formatting.** There is no rule that every commit gets a co-author. Owner-only, automated dependency, or genuinely unknown contributions may have no `Co-authored-by` or `Reviewed-by` trailers. Do not use a bot's reviewer request as evidence that it reviewed anything.
2. Only an **owner-approved attribution ledger** can change commit messages or PR bodies. Store original SHA, contribution type, evidenced role, approved trailer identity, evidence pointer, proposed/approved/rejected decision and the approver. Session models are optional; never infer from present-day defaults, labels or tool configuration.
3. `Reviewed-by` is a *substantive, evidenced review*, **not** GitHub `APPROVED`. Do not backdate PR reviews, pretend previous approval happened, or edit historical conversation to rewrite the narrative.
4. The offline Git transformation must be **message-only**, idempotent in specification and deterministic for a frozen source. The rewritten DAG will have different SHAs, even for unaffected descendants. Preserve commit tree IDs and parent order via old→new mapping. Existing commit/tag cryptographic signatures cannot be transplanted to changed objects.
5. GitHub PRs, discussions, checks, approvals, releases, Pages links, and GitHub-owned `refs/pull/*` are **not** part of a writable Git clone. Preserve an independently exported record of the original GitHub history and append labelled retrospective attribution to PR bodies only after checking exact originals. **A force-push may make historical PR diffs, review anchors and commit links stale or inaccessible; GitHub will not re-anchor them to rewritten commits.** Obtain explicit owner acceptance of that irreversible presentation/provenance loss, including unmerged PRs.
6. Old immutable SHA references may remain fetchable temporarily or persist in GitHub internals; **do not count on their reachability**. Update **both in-repository and downstream** trusted-SHA consumers as a coordinated post-rewrite fix-forward, with their own verification. Updating a hard-coded SHA inside a commit changes that commit's SHA again: do not attempt to self-pin the final commit or silently fold pin updates into the message-only rewrite. Select an already published, verified trusted-assets commit as the pin target.
7. Do not change package versions, model tiers, source code, secrets or unrelated repositories during the rewrite. Any necessary fix-forward is a separate ordinary PR before freeze or after restoration.

## Verified environment and notable consumers

- `Brewfile.dev` already installs `git-filter-repo` and `bfg`: do not introduce a competing rewrite engine or install scripts without checking the host's actual installed version (`git filter-repo --version`; inspect supported flags/format).
- `dot_gitconfig.tmpl` enables `commit.gpgsign=true`; a rewrite cannot preserve historical commit signatures as valid signatures. Archive the original signed objects. Record this as an explicit owner decision.
- `.github/workflows/agentic-review.yml`, `scripts/onboard-agentic-review.py`, `.github/workflows/copilot-setup-steps.yml`, downstream `uses` / `trusted_ref` pairs and asset manifests have SHA-sensitive trust contracts. The known downstream set is `me`, `pwa`, `pseudoimage`, `pseudolocalize`, `lwip` and `slamscan`; search for additional consumers, forks, code snippets, releases and local checkouts. Re-onboard each from **one new verified canonical trusted-assets commit** using the established tool, including any dotfiles self-pins; do not hand-edit only one of `uses`, `trusted_ref`, the Copilot setup pin, or the associated manifest.
- `main` uses active non-fast-forward protection and required-PR/status-check rulesets; refresh permissions and any bypass policy immediately before cutover. No CI green result substitutes for operational acceptance.
- As of the baseline, an unrelated Basic Memory stack (#147 then #148) and other development were still present. Do not assume the repository is frozen; open PRs and worktrees must be dispositioned explicitly.

## Gate 0 — audit without modifying remote or local-owned state

**Owner:** one named coordinator; **helpers:** read-only attribution investigators.

Record current `main` SHA, all branch/tag/note/replace refs, worktrees, unpushed commits, stashes **without modifying them**, Git LFS pointers, releases, open/closed PRs, review threads, branch rulesets, GitHub Actions status, downstream immutable SHA pins and any third-party forks. Distinguish reachable `main` commits from other refs, and GitHub-owned PR refs from writable branches. Capture a dated inventory and where each fact came from.

Read-only discovery examples:

```bash
git remote -v
git status --porcelain=v1
git worktree list --porcelain
git for-each-ref --format='%(refname) %(objectname)'
git log --all --format='%H%x09%an%x09%ae%x09%s'
git count-objects -v
git config --show-origin --get commit.gpgsign || true
gh api -X GET --paginate repos/randytarampi/dotfiles/pulls -f state=all -f per_page=100
gh api repos/randytarampi/dotfiles/rulesets
# Search every downstream consumer for literal GitHub workflow SHAs and trusted_ref.
```

Use `gh api -X GET` with query fields: without `-X GET`, `-f` makes a request POST. Confirm CLI syntax on the host. Do not run discovery scripts from unknown PR code with credentials. For private forks, protected refs, private session stores and additional accounts, obtain separate owner access; a public mirror cannot prove completeness.

**Exit:** complete ref/consumer inventory, explicit disposition for every active PR and local worktree, no missing private branches. Record signed tags/releases, GitHub PR review anchors, LFS objects and all pinned workflow trust roots as separate checklist entries. UNKNOWN is an acceptable discovery result, not permission to proceed to a destructive phase.

## Gate 1 — evidence-backed attribution ledger

Collect parallel read-only claims from the agents that actually did the work. Each claim must identify the **original commit SHA**, original author, affected paths, agent/tool and verified model where known, role, source/session evidence pointer, confidence, duplicate/conflict group, and requested change. For PR-body claims record original PR number and original body hash. Suggested statuses: `proposed`, `corroborated`, `contested`, `unknown`, `owner_approved`, `owner_rejected`.

Keep private transcripts and credentials out of the repo, public PRs and Plannotator links. A public ledger may contain only sanitized evidence references. The operator verifies proposed trailer identities against current policy; no invented e-mails or human identity impersonation. Separate roles:

- **Additional implementer/designer who materially wrote or revised this commit:** eligible for `Co-authored-by` with an approved identity.
- **Substantive reviewer:** eligible for `Reviewed-by` when the review is corroborated and scoped; PR prose credit may be more accurate for after-the-fact GitHub reviews.
- **Owner directed, merged or verified but did not co-author:** document role at PR level, not invented co-authorship.
- **Sole-human commit or autonomous Dependabot contribution:** no additional trailer required.
- **Multiple agents claiming the same work:** allow multiple genuinely material contributors; flag duplicate/conflicting claims to the owner and do not auto-award credit based on whichever model last wrote the file.

Never add a trailer for a model that was merely installed, available or selected as a theoretical fallback. An agent's self-report is a lead to corroborate, not an exclusive authority. **Only signed-off `owner_approved` ledger entries can feed the rewrite.** Freeze ledger content and record its cryptographic digest before simulation.

## Gate 2 — private backups and offline rehearsals

**Owner approval required** for archives and scratch clones; no GitHub ref mutation.

In a private directory independent of the user's working clone:

```bash
git clone --mirror https://github.com/randytarampi/dotfiles.git original.git
git -C original.git show-ref > refs-before.txt
git -C original.git bundle create ../dotfiles-before.bundle --all
git -C original.git bundle verify ../dotfiles-before.bundle
git clone --mirror original.git candidate.git
sha256sum dotfiles-before.bundle  # or shasum -a 256 on macOS
```

A mirror alone does not capture unpublished local commits, local stashes, private-only refs, Git LFS content, GitHub's stored PR review database or every historical GitHub PR object. Preserve owner-local refs separately with consent, and archive LFS objects and all affected annotated/signed tags explicitly. Export full PR bodies, review submissions, inline threads, comments, releases and check/workflow references through authenticated GitHub APIs, retaining exact original bodies, timestamps and SHA references for drift checks. Test recovery by restoring a sample ref **in a separate private test clone**; never test recovery by moving live `main`.

Treat the original mirror/bundle as immutable evidence: encrypt/restrict access, store off the working machine if appropriate, retain through owner acceptance and never force-push it as a mirror to GitHub.

## Gate 3 — deterministic transform, in isolation

Apply exactly the approved message changes through the installed `git-filter-repo` commit callback or a separately reviewed equivalent. It must read a **frozen ledger by full original SHA**, reject unexpected original SHA/identity mismatch, normalize only approved terminal `Co-authored-by` / `Reviewed-by` blocks, retain all unapproved trailers, and refuse malformed/ambiguous messages rather than silently guessing.

- Do **one pass** over the approved explicitly enumerated refs. Do not independently rebase all 1,000+ commits or rewrite each agent's branch first.
- Never include `refs/pull/*` or all refs from a mirror in a push. Be explicit about annotated and signed tags, Git notes, `refs/replace`, release pointers and fork-owned branches; these need owner decisions, not default recreation/deletion.
- Collect `git-filter-repo`'s commit/ref mapping files and verify their format against the installed version. Preserve those files outside the repository with the frozen ledger digest.
- For every original→rewritten commit: compare tree OID, Git author/committer names, emails, timestamps, parent count/order using mapped parents; verify **all source blobs unchanged**, not only HEAD. Audit the root and merge commits. Check signatures/annotated tags, count of commits and `git fsck`.
- The final rewritten `main^{tree}` must equal frozen original `main^{tree}`. Do not assume `make verify` passing implies this graph-level invariant.
- Independently rerun validation in a separate clone/worktree, using `make verify` on the rewritten committed tree where dependencies permit. The public GitHub required checks and review workflows cannot be called **verified on rewritten `main`** until the actual published refs and trusted SHA pins are qualified; report offline evidence separately and do not infer CI from identical trees.

**NO-GO** on missing or duplicate old→new mappings, tree mismatch, unapproved message edits, unhandled signed tags, privacy leak, missing downstream consumer or inability to recover the old graph.

## Gate 4 — prepare GitHub-side and downstream updates, still offline

Prepare a full PR-body patch per approved PR ID, with exact old body hash and additive **Contributors and roles** section. Preserve original outcome, narrative, bot comments, attachments and links. The PR's actual reviews and approvals remain immutable historical events. Label retrospective notes; do not claim a review of a new SHA occurred in the past. **GitHub REST PR-body updates have no atomic compare-and-swap:** re-read each body immediately before the update, serialize writers, stop on mismatch, log returned response and re-read afterwards; a race remains possible, so preserve reversibility. Updating historical PRs can trigger webhooks and notifications: audit/disable only specifically relevant automations with owner approval. For PRs never created (direct-to-main work), retain Git attribution and the ledger; do not fabricate PRs.

For the dotfiles repository itself **and** every affected downstream repository, prepare separate ordinary fix-forward updates to the trusted SHA callers, `trusted_ref`, Copilot fallback and any other pinned assets, based on **one verified post-rewrite trusted-assets revision**. Use `scripts/onboard-agentic-review.py` as appropriate, validate generated diffs and re-evaluate asset manifests. Keep these contents-changing commits **outside** the message-only history transform; the final rewritten `main` tree equality test applies immediately after the transform, not after approved fix-forward commits. Do not merge downstream updates against old `main` prematurely.

Write a **cutover manifest** listing old and new exact tips for every proposed writable ref, the signed owner-approved ledger digest, each downstream owner, release/tag disposition, validation owners, rollback command templates and the allowed maintenance window. No open worktree or PR may be left with unclassified state.

## Gate 5 — owner-only freeze and force-with-lease publication

At the freeze, stop concurrent development, scheduled writer jobs, bot reviewer dispatch and downstream re-onboarding. Confirm local and remote tips, exact rulesets/bypass rights, old/new hash mapping, bundle verification, public PR body drift and active PR status. **If any remote tip changed since rehearsal, stop and regenerate.**

Only the named owner/operator may publish, after an explicit approval naming the cutover manifest digest and refs. Push *only explicitly whitelisted writable refspecs*, using per-ref `--force-with-lease=refs/heads/NAME:ORIGINAL_SHA`, and never `git push --mirror`, `git push --force --all`, or writes to GitHub-owned `refs/pull/*`. Avoid blanket rule disablement and record every bypass event. Decide an ordering that allows dependent workflows to be rolled forward; a staged canary cannot fully simulate history replacement on protected `main`.

After verifying remote refs, execute the separately approved **in-repository pin repair**, then coordinate downstream pin PRs and smoke tests before resuming normal write traffic. Publish prepared additive PR-body patches only with serialized read-before-write and read-after-write checks; stop on drift. Reconcile remaining approved feature branches against the new history without discarding private/dirty state. A green skipped workflow is not acceptance; a restored workflow pin is not proof of live review publication.

## Gate 6 — acceptance and rollback

Require rewritten **pre-fix-forward** `main` tree equality, `git fsck`, mapping completeness, explicit approval of subsequent pin-repair source diffs, checks and a real downstream review consumer where applicable. For dotfiles, run `make verify`, required CI (`ci/required`), security checks (`security/required`), and check actual OpenCode/Junie/Copilot publisher results; separately label Gemini as verified or UNKNOWN depending on a genuine non-skipped run. Check GitHub PR diffs/threads, release/tag references, clone recovery, installed `chezmoi` source and downstream caller SHA pins. Recheck updated branch protections.

Rollback is another coordinated non-fast-forward update, never `git reset --hard` on another contributor's work. Restore only exact prior refs with leases against **newly published** tips from the independent bundle, revert downstream SHA pin changes and PR-body amendments using saved originals **only when unchanged since publication**, and repeat validation. If downstream has already consumed new revisions, consider forward repair rather than a flip-flop. Preserve both graphs and their mapping until formally accepted.

## Deferred vs. explicitly forbidden

**Defer:** building a fully automatic attribution inference system; retroactive GitHub approvals; guessing missing agent models; attempting to re-sign old commits as their original authors; deleting unmerged test branches, stashes or agent worktrees; archival retention changes; changes to unrelated providers/reviewers; rewriting other repositories merely to improve cross-repo links.

**Never:** auto-approve conflicting credit; silently rewrite unknown history; publish private session dumps; treat reviewed-by as a formal GitHub approval; insert meaningless coauthors on owner-only commits; manufacture original PR timestamps; blanket-remove protection; force-push over a changed live tip; push mirror refs; claim that old SHA links and check-runs were updated by rewriting Git.

## Operator handoff and evidence record

At each gate, the local lead records: frozen `main` and refs, GitHub PR inventory, owner decisions, ledger digest and status counts, executable commands *actually run*, files/checksums, unit/graph/CI evidence, affected downstream consumers, current blockers and exact next safe action. Keep this report dated and private when it contains sensitive session references.

Any local agent can **coordinate and prepare** gates 0–4 with owner direction; publication gate 5 and destructive recovery remain explicitly owner-controlled. Another agent must independently verify the mapping and sample attribution decisions before the push. A complete read-only simulation with a justified **NO-GO** is a successful result when provenance or consumer safety cannot be established.

## Relevant references

- [Original contribution policy PR #91](https://github.com/randytarampi/dotfiles/pull/91)
- [Official git-filter-repo](https://github.com/newren/git-filter-repo)
- [GitHub rulesets and protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets)
- [GitHub REST pull-request editing](https://docs.github.com/en/rest/pulls/pulls)
- [GitHub rebase-and-merge mechanics](https://docs.github.com/en/pull-requests/using-pull-requests/about-pull-request-merges)
