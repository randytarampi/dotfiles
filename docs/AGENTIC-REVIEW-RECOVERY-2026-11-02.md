# Agentic review recovery and downstream rollout — 2026-11-02

**Status:** draft handoff / runbook. Do not treat this as a claim that the new
OpenCode `free` pool or Gemini App publisher has been live-validated.

## Mission and verified baseline

Restore dependable opt-in reviewer execution across
`randytarampi/dotfiles`, `me`, `pwa`, `pseudoimage`,
`pseudolocalize`, `lwip`, and `slamscan`, without broadening reviewer
permissions, hiding failed model calls, inventing free-tier entitlements,
or duplicating bot comments. At investigation time, dotfiles `main` was
`5409ed2669b2ab4b9dc54a350db2c0e66f8d914b`. **Re-query all branch heads,
current checks, and review comments before each rollout.**

### Distinct failure modes — do not conflate them

| Evidence | Observation | Disposition |
|---|---|---|
| [run 41308041751](https://github.com/randytarampi/dotfiles/actions/runs/41308041751) | `gemini` 503 service overload followed by free-tier HTTP 429 (20 requests/day on `gemini-3.8-flash`); notifier failed closed; OpenCode was skipped | Live Gemini capacity issue; **not** an OpenCode inference-pool failure |
| [run 41310733834](https://github.com/randytarampi/dotfiles/actions/runs/41310733834) | Gemini 429; Junie and Copilot jobs succeeded; notification correctly marked requested Gemini failure | Aggregate review should not require Gemini unless explicitly selected |
| [run 41308041023](https://github.com/randytarampi/dotfiles/actions/runs/41308041023) | Parser succeeded; all reviewers skipped | Green run is not evidence of review |
| Downstream runs | All six repositories' legacy dispatcher callers lack required `trusted_ref` and `id-token: write` and report `startup_failure` | Repair each caller's reusable workflow contract before diagnosing models |
| [PR #145](https://github.com/randytarampi/dotfiles/pull/145) | Host OpenCode v2.2.5 rejects some explicit nested LiteLLM CLI model IDs with `Unsupported package` while listing models; in-session routing works | Hosted `review-free` consumer needs a real test, not static-only proof |

[Issue #106](https://github.com/randytarampi/dotfiles/issues/106) remains the canonical
Gemini dedicated-App publication proof backlog. Prior Junie App success on
PR #105 does not prove Gemini success.

## Linear dotfiles stack

1. **PR #152 (base: main):** change `review-all` to Junie+Copilot; keep Gemini
    explicit and failing visibly when requested.
2. **PR #155 (base: PR #152 head):** pin Gemini CLI version and expose
    `GEMINI_REVIEW_MODEL` Actions-variable override; test an actual eligible
    model/quota rather than assuming the default is free.
3. **PR #159 (base: PR #155 head):** actionable downstream rollout, credentials
    audit, version/security gates and recovery notes.
4. **PR #161 (base: PR #159 head):** separate hardening of the Gemini
    reviewer's `--yolo` tool access; owner must test actual tool denial on
    untrusted PR content before relying on that security guarantee.

The four dotfiles PRs are linearly stacked. Review only each PR's delta.
Merge in order, updating base/refs only after verified required checks.
**A single GitHub PR ancestry cannot span repositories:** each downstream
repository needs its own separately reviewed implementation PR. Do not
pretend those satellites can all share one `base` branch.

## Phase 1 — verify and promote central stack

- Read `AGENTS.md`, `configs/agents/repo-agents-shared.md`,
  `configs/review/code-review-prompt.md` and the PR template.
- Check current head/base, exact CI/CodeQL, review threads, and assets manifest.
  Run `make verify` on the committed code and `make check-ci-assets` for any
  trusted prompt/manifest change.
- Establish whether the baseline `@main` dispatcher can perform an actual
  authorised `/opencode --pool=opencode` request and publication. A skipped
  review job is *not* success.
- For the hosted `--pool=free` route, confirm the runner-local LiteLLM
  version, its upstream routing semantics, actual OpenCode v2 provider
  compatibility and provider credentials. Probe a genuine review plus tool
  call, forced 429 to an independent deployment, and correct OIDC/App PR
  publication. `/v1/models` HTTP 200 does **not** prove inference.
- If the OpenCode custom provider fails with `Unsupported package`, fix the
  actual OpenCode v2 transport/SDK interaction in a separate small fix-forward
  before widening routing. Do **not** mask it by falling back to a charged
  route or granting write permissions.
- Do not change `configs/review/assets-manifest.json` or immutable trusted
  anchors merely to make CI pass. If trusted rubric assets change, regenerate
  manifest and advance the anchors only in a separately verified follow-up.

## Phase 2 — re-onboard downstream caller workflows (one PR per repo)

Generate each caller from the repository's existing supported tool,
`scripts/onboard-agentic-review.py`; **do not** hand-clone a stale caller:

```bash
# Run from a clean, verified dotfiles checkout (after central stack merges).
# Only a SHA available from the canonical repository may anchor callers.
git fetch https://github.com/randytarampi/dotfiles.git main
trusted_ref="$(git rev-parse FETCH_HEAD)"
git ls-remote https://github.com/randytarampi/dotfiles.git refs/heads/main
# Verify the selected commit can be fetched by its exact SHA.
git -c protocol.version=2 fetch https://github.com/randytarampi/dotfiles.git "$trusted_ref"
test "$(git rev-parse FETCH_HEAD)" = "$trusted_ref"
python3 scripts/onboard-agentic-review.py --repo /path/to/me \
  --ref "$trusted_ref" --dry-run

# Then explicitly apply in that repository's isolated implementation worktree:
python3 scripts/onboard-agentic-review.py --repo /path/to/me \
  --ref "$trusted_ref" --workflows-only
```

Repeat for `pwa`, `pseudoimage`, `pseudolocalize`, `lwip`, and
`slamscan`. The script also generates the pinned Copilot setup and portable
review skill; inspect each changed file and any existing user changes.
Ensure the destination is the actual repository root with its own `.git`.

Required caller contract:
- Immutable, **matching** 40-hex SHA in
  `uses: randytarampi/dotfiles/.github/workflows/agentic-review.yml@SHA` and
  `with.trusted_ref: SHA`.
- `event_json: ${{ toJson(github.event) }}` and `secrets: inherit`.
- `contents: read`, `id-token: write`, `pull-requests: write`,
  `issues: write`, without increasing contents permissions.
- Existing bot-sender exclusion, authorised triggers, and repository-specific
  unrelated workflows remain intact.

**Per-repository acceptance:** run actionlint and its normal CI, then
request an authorised `/opencode --pool=opencode` review on a disposable,
same-repository PR. Prove the parse and model jobs both execute, official
OIDC review publishes for the expected PR head, no changes/push occur, and
repeat invocation behaves as documented. A `startup_failure` is a blocker.

## Phase 3 — variables, secrets and GitHub Apps

The source repository's GitHub Actions variables endpoint was **not readable
through the installed ChatGPT GitHub connector**, so **none have been copied**.
Review and use the local authenticated GitHub CLI for exact variable transfer.
The earlier `sync-dotfiles-actions-variables.sh` helper is a **separate
conversation attachment**, not a committed repository file; inspect it
before use or use the following narrowly scoped commands:

```bash
gh api --paginate repos/randytarampi/dotfiles/actions/variables \
  --jq '.variables[].name'
# Inspect only required variable names and destination current values before writes.
gh variable list -R randytarampi/me
# Apply a specifically approved variable using gh variable set -R OWNER/REPO.
```

Do not blanket-copy unrelated source-repository variables. Candidate names
include `CLOUDFLARE_ACCOUNT_ID`, `JUNIE_GITHUB_APP_ID`, and
`GEMINI_GITHUB_APP_ID`. Identify actual source names and applicability first.
A variable is not a credential secret and is not automatically safe for all
repositories; the Cloudflare account identifier, for example, is account
specific. No secret values should enter a PR description, report, or test log.

**Secrets are not copied by the variables API**. Independently provision
only the applicable `OPENCODE_API_KEY`, `MISTRAL_API_KEY`, `GROQ_API_KEY`,
`CLOUDFLARE_API_TOKEN`, `NVIDIA_NIM_API_KEY`, `OPENROUTER_API_KEY`, `GEMINI_API_KEY`,
`JUNIE_API_KEY`, and dedicated App private keys to each destination.
Check GitHub App *installation* and permission on each destination as
well as the repository's own Actions token permissions. Never assume a
secret configured in dotfiles is inherited by a *different* repository's
`secrets: inherit` call. Avoid enabling NVIDIA for unattended review:
developer-use permissions must remain explicitly qualified.

## Phase 4 — reviewer model and version compatibility

At analysis time, upstream LiteLLM's verified latest release was
`v1.106.6`, while the runner-local review job used `1.102.1`.
The Gemini action `@v0` defaults to a floating `latest` CLI;
its inputs include `gemini_cli_version` and `gemini_model`.
The reviewed Gemini CLI candidate is `0.66.0`; the default model was corrected from access-restricted `gemini-2.5-flash-lite` to `gemini-3.5-flash-lite` after reviewing Google's current models documentation. JetBrains Junie action
release observed was `v1.7.21`. **Recheck current upstream versions before
a new PR.** A newer version number does not prove compatibility.

- Prefer a pinned, tested Gemini CLI and account-qualified model,
  controlled by Actions variable `GEMINI_REVIEW_MODEL`. A 429/503 on an
  expressly requested Gemini review must still fail visibly.
- Gemini's `GEMINI_CLI_TRUST_WORKSPACE=true` on PR-owned source deserves a
  separate security review. Restrict arbitrary shell execution, project
  hooks/extensions, and code editing. Follow upstream run-gemini-cli trust
  guidance. Do not broaden permissions or pass tokens into PR-controlled
  processes merely to obtain a review.
- Junie and Gemini normal lanes must be read-only. Notify must be scoped
  to the intended App identity and update only App-owned markers;
  foreign markers must fail closed. OpenCode official OIDC publication is
  independent of the notify publisher.
- For `review-all`, prefer verified reviewers, not an imaginary single
  cross-agent fallback chain. The new OpenCode `free` model group and
  standalone Gemini CLI have **different** runtimes and retry mechanisms.

## Negative cases and acceptance ledger

| Case | Expected | Evidence owner |
|---|---|---|
| Unauthorised or bot-authored command | No reviewer, no secrets, no publication | Workflow test + GitHub event |
| Label-only `review-opencode` | No native OpenCode review (unsupported event) | Live skip |
| `review-all` with unavailable Gemini | Junie/Copilot can succeed independently | PR #152 test + live run |
| Explicit Gemini 429 or 503 | Job and notifier signal failure, no empty review comment | PR #155 test + owner run |
| Downstream caller without pinned ref or OIDC permission | Detect in onboarding/CI, never silently bypass | Each satellite PR |
| Missing optional keys | Safe OpenCode Big Pickle fallback in `free` pool, not invalid config | Synthetic selector test |
| Two qualified independent provider keys | LiteLLM router starts; real forced-429 probe moves to another deployment | Owner live probe |
| Unqualified/costly model | Do not claim zero-billing solely because key exists | Owner account dashboard |
| New PR or newer head | Review targets intended head and does not mutate branch | Live App comment + git state |
| Already-owned marker | Update only correct GitHub App's own marker | Junie/Gemini publication test |
| Empty summary | No misleading success or empty review post | Notify unit test |

## Rollback and stop rules

- Revert central policy PR independently if default aggregate membership is
  objectionable; keep explicit Gemini trigger.
- Pin Gemini back to the last verified CLI/model if it regresses; retaining
  failure reporting is essential.
- Revert each downstream caller PR independently. Recheck immutable
  trusted-ref/manifest pairing before retrying.
- Stop after two unsuccessful repair attempts per subsystem; record exact
  logs and what failed. Do not merge/resolve threads solely from green lint.
- Never delete user-owned credentials, change GitHub App permissions without
  an explicit need, or publish full Actions environment dumps.

## Contributors and roles

- **Maintainer:** requirement and target repositories; owns account credentials,
  live provider usage, repository review and merge approval.
- **ChatGPT (GPT-6):** November 2 investigation, central implementation,
  tests and handoff documentation.
- **Previous Junie/OpenCode contributors:** their existing review and v2
  cutover work is preserved; no new review or consultation is claimed.
