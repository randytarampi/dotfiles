# Agentic Review on GitHub

Agentic PR review for this repo and downstream repos (`me`, `pwa`, `pseudoimage`,
`pseudolocalize`, `lwip`, `slamscan`), built on the three official agentic
GitHub Actions plus GitHub Copilot's native reviewer. All reviewer triggers are
opt-in (labels or mentions) — nothing runs on every push.

The shared review prompt lives in `configs/review/code-review-prompt.md` and is
used by both the GitHub Actions and local review lanes.

## Reviewer lineup

| Reviewer | Action | Trigger | Secret | Job |
|---|---|---|---|---|
| GitHub Copilot | native reviewer | add as reviewer, or `review-copilot` label | none | broad correctness; reads `AGENTS.md` and repo MCP/skills |
| OpenCode | `anomalyco/opencode/github` | `/oc <prompt>` or `/opencode <prompt>` PR comment | `OPENCODE_API_KEY` | local preset roles via explicit model + provider blocks, MCP mirror, skills, codegraph |
| Junie | `JetBrains/junie-github-action@v1` | `@junie-agent <prompt>` mention, or `review-junie` label | `JUNIE_API_KEY` | shared review method (custom-prompt mode with GitHub context attached) |
| Gemini | `google-github-actions/run-gemini-cli@v0` | `@gemini-cli /review` mention, or `review-gemini` label | `GEMINI_API_KEY` | behavior regressions, missing tests, operational risk |
| Copilot auto-request | REST job in the reusable workflow | `review-copilot` label | none | requests `copilot-pull-request-reviewer[bot]` |

`review-all` requests Junie, Gemini and Copilot on a label event. OpenCode's official action cannot handle that event and is skipped; request OpenCode with an authorized `/opencode` or `/oc` PR comment. Other reviewers retain their current opt-in label/mention support.

## Labels

- `review-opencode` — legacy label; official OpenCode action does not run on label events. Use an authorized `/opencode` PR comment.
- `review-junie` — Junie code review
- `review-gemini` — Gemini review
- `review-copilot` — request Copilot as reviewer
- `review-all` — all of the above

## Mentions

- `/oc <prompt>` or `/opencode <prompt>` — OpenCode treats the text as the
  primary task, with the shared review prompt as guidance
- `@junie-agent <prompt>` — Junie answers the ad-hoc task with GitHub context
- `@gemini-cli <prompt>` — Gemini treats the text as the primary task, with the
  shared review prompt as guidance

Mention text (minus the trigger token) is passed as the primary task. Labels
select the standard review prompt from `configs/review/code-review-prompt.md`.
All three agentic lanes (OpenCode, Junie, Gemini) run that shared method —
Junie receives it as prompt text since it executes on JetBrains' backend and
sees no local files. Copilot reads the method via the installed
`.github/skills/code-review/SKILL.md` (inlined fallback) plus its own
repository instructions.

## How it works

- `.github/workflows/agent-review.yml` — stable dispatcher stub for this repo.
  It declares trigger events and permissions, forwards raw event JSON, and
  filters bot senders in defence in depth. Trigger parsing is centralized in
  the reusable workflow, so parser improvements arrive through the dotfiles
  ref. Because the ref is immutable, downstream callers must be re-onboarded
  whenever the trusted workflow revision advances; the onboarding script writes
  the new ref and matching `trusted_ref` and Copilot setup default together.
- `.github/workflows/agentic-review.yml` — reusable `workflow_call` workflow.
  Its first `parse` job handles dispatcher-mode event JSON and direct
  passthrough calls. Downstream repos carry only the stable stub installed from
  `configs/review/dispatcher-stub.yml`:

  ```yaml
  jobs:
    review:
      uses: randytarampi/dotfiles/.github/workflows/agentic-review.yml@<immutable-40-hex-sha>
      with:
        agents: "opencode,junie,gemini,copilot"
        trusted_ref: <same-immutable-40-hex-sha>
      secrets: inherit
  ```

  To adopt elsewhere, run `scripts/onboard-agentic-review.py --repo <path> --ref <40-hex-sha>`.
  It installs the stable stub and writes the same immutable SHA into the reusable
  workflow `uses:` pin, `trusted_ref`, and Copilot setup default; mutable refs
  such as `@main` are not accepted.
  Re-onboarding is required whenever the trusted workflow revision advances,
  as well as when trigger events or permissions change.
  The CI OpenCode lane runs the explicitly selected model with all three
  provider keys available. The repo's local fallback policy is a runtime
  plugin concern and is not part of the CI config.
  OpenCode, Junie, and Gemini stage the trusted dotfiles commit identified by
  the caller-provided immutable `trusted_ref` and verify its asset manifest
  before using prompts, configuration, skills, or `ci-codegraph.sh`.
  Copilot setup uses a baked-in immutable 40-character dotfiles SHA for automatic
  runs and accepts an optional manual-dispatch override; PR content never
  participates in prompt-file loading.
- Security posture: minimal per-job permissions, bot-sender filtering, and
  trusted review assets from an immutable SHA. OpenCode needs `contents: read`
  and `id-token: write` to exchange GitHub OIDC for the installed OpenCode App
  token. Its official action posts comments itself and the plan agent is
  configured to deny edits, bash and subagent delegation; a local pre-push hook
  blocks ordinary pushes. The App token can technically write according to
  its installation permissions, so this is not a cryptographic read-only
  sandbox. Only maintainer-authorized triggers may launch it. Junie and Gemini
  use read-only `github.token` and pass their results to the separate `notify`
  publisher. The manual fix lane remains separate, explicitly gated and
  owner-authenticated, generation-read-only, gated by the `agentic-review-fix`
  environment, validates paths and gitlinks, then publishes only the
  exact-path allowlist on a unique `agentic-review-bot/<run-id>` branch and
  opens or updates an idempotent draft PR. It is dotfiles-only and is not
  installed by the onboarding script. The Copilot lane stays orchestration-only
  — Junie may commit and push only through the separate, manually approved fix
  lane; the normal Junie review lane is read-only. Copilot's own write-back is
  governed by repo Settings → Copilot → Agent permissions, not by this workflow.

### Action version policy

External actions, including third-party actions, use verified floating major
tags (`@vN`). Major tags can move; this intentionally accepts compatible
upstream changes in exchange for updates without manual SHA churn. The
repository-scoped `zizmor.yml` ref-pin policies and offline action-ref
tests enforce this choice, and weekly Dependabot updates remain enabled.

The owned dispatcher is the sole exception: it invokes this repository's
reusable review workflow at `@main`. Its `trusted_ref` remains a 40-character
immutable commit verified against the review-asset manifest. The scoped Zizmor
exception is guarded by the test that restricts that `@main` use to the
dispatcher only.

## Manual fix lane

`agentic-review-fix.yml` is deliberately not distributed by
`scripts/onboard-agentic-review.py`; it is a dotfiles-only, `workflow_dispatch`
lane. The owner must provide:

- `base_sha`: an existing 40-hex repository commit. Both jobs check out and
  verify this exact commit.
- `allowed_paths`: a newline-separated list of exact repository-relative paths.
  The generator inventories tracked changes, deletions, and untracked files,
  rejects anything outside this set, and includes approved untracked files in
  the uploaded patch. The publisher repeats the exact-path and mode `160000`
  checks before applying and staging it.
- `trusted_ref`: an immutable 40-hex dotfiles commit containing the verifier.

The generator runs `opencode run` directly with a read-only token and only
edits its workspace. It installs the repository-pinned OpenCode CLI
(`opencode-ai@1.18.34`) and verifies both its executable and reported version;
it does not commit, push, or create a PR. After the
`agentic-review-fix` environment approval, the publisher creates the unique
`agentic-review-bot/<run-id>` branch. On reruns it records the current remote
tip and uses an exact-ref `--force-with-lease` update; a changed tip fails
closed. It updates an existing open draft PR for that head or creates one when
absent.

## Secrets

Set per repo (Settings → Secrets and variables → Actions):

- `OPENCODE_API_KEY` — OpenCode Zen (used by the OpenCode job and its
  cross-provider fallbacks)
- `JUNIE_API_KEY` — Junie backend token (from junie.jetbrains.com/cli)
- `GEMINI_API_KEY` — Google AI Studio
- `OPENROUTER_API_KEY` — OpenRouter (consumed by OpenCode's `free`
  fallback chains in CI; also usable by Junie BYOK if preferred)

- OpenCode publishing uses the installed [OpenCode GitHub App](https://github.com/apps/opencode-agent) through OIDC; **no `OPENCODE_APP_ID` or `OPENCODE_APP_PRIVATE_KEY` secrets are needed**. Callers must grant `id-token: write`.
- Junie/Gemini do not require publisher App credentials. The `notify` job
  obtains the job-scoped `GITHUB_TOKEN` with `pull-requests: write` and posts as
  `github-actions[bot]`. Generation jobs retain `contents: read` only.
  GitHub API failures, missing publication permission, and empty model outputs
  fail visibly; unrequested events do not start publication.
- OpenCode uses the **official** `anomalyco/opencode/github` composite action,
  pinned to its upstream `latest` tag's resolved commit. The action invokes
  `opencode github run`, exchanges the runner's OIDC token for the installed
  App's token, and manages its own PR comments. The action installs its CLI
  dynamically; the action SHA does not freeze that binary. The `plan` agent,
  `share: false`, denied mutating tools and pre-push hook are review safety
  controls. Native publication does **not** produce the old
  `<!-- agent-review:opencode -->` marker or guarantee comment deduplication.
  The native action accepts `/opencode` and `/oc` **PR conversation issue_comment events only**; inline review comments are not qualified and do not start this lane.
  Label events are not valid OpenCode action inputs and never run this lane.
- Junie's `silent_mode: "true"` exposes its `junie_summary` for the separate
  `notify` job, which publishes Junie/Gemini bodies using the GitHub Actions
  bot identity and marker-based updates. Copilot uses its native reviewer.
  Avoid PAT-backed user publishers: unlike `github-actions[bot]`, they might
  retrigger the dispatcher.

## MCP servers in CI

The OpenCode CI config `configs/opencode/ci/opencode.json` mirrors the local
default MCP set, minus local-only servers:

| Server | Type | Notes |
|---|---|---|
| `context7` | remote | anonymous; no secret |
| `github` | remote | `Authorization: Bearer {env:GITHUB_TOKEN}` |
| `grep` | remote (`https://mcp.grep.app`) | the `gh_grep` MCP; anonymous |
| `codegraph` | local (`codegraph serve --mcp`) | binary installed by `scripts/ci-codegraph.sh` |
| `idea` | excluded | local IDE only |
| `sentry` | excluded | no secret in CI scope |

For the repo **Settings → Copilot → MCP servers** UI (no API for this — manual, per repo), the
config blocks need explicit `type` fields; Copilot's schema accepts `local`/`stdio`/`http`/`sse`
and rejects typos like `sdio` (verified live 2026-09-05):

```json
{
  "codegraph": { "type": "stdio", "command": "codegraph serve --mcp", "tools": ["*"] },
  "context7": { "type": "http", "url": "https://mcp.context7.com/mcp", "tools": ["resolve-library-id", "get-library-docs"] },
  "grep": { "type": "http", "url": "https://mcp.grep.app", "tools": ["*"] },
  "github": { "type": "http", "url": "https://api.githubcopilot.com/mcp", "tools": ["*"] }
}
```

`scripts/ci-codegraph.sh` installs `@colbymchenry/codegraph` (npm), skips if
`codegraph` is already on `PATH`, then runs `codegraph sync` (falling back to
`codegraph index`). The `.codegraph/` index is cached with `actions/cache`,
keyed per-repo by source revision.

## Codegraph for GitHub Copilot

Copilot code review runs in an ephemeral GitHub Actions environment, so the
same local-command MCP pattern works there:

1. `.github/workflows/copilot-setup-steps.yml` (workflow_dispatch) installs
   codegraph and warms the shared `.codegraph/` cache. Copilot uses this to
   customize its review/agent environment.
2. MCP servers are configured in **repo Settings → Copilot → MCP servers**
   (GitHub-managed; not a committed file). Add `codegraph` as a `local` server
   with command `codegraph serve --mcp` and explicitly allowlisted read-only
   tools. Remote servers (context7, grep) need no install.
3. Any MCP secrets must use the `COPILOT_MCP_*` prefix (Agents secrets).

Copilot code review also reads repo instructions (`AGENTS.md`, `REVIEW.md`)
natively; keep review posture guidance there for the Copilot lane.

## Review skill

`.github/skills/code-review/SKILL.md` is the committed review rubric
(verify-first, blocking-vs-suggestion, evidence rules, conventions). The
  reusable workflow stages a fixed commit and copies the skill into
  `.opencode/skills/` at runtime (`.opencode/` is
gitignored).
Copilot reads equivalent guidance from repo instructions. Tweak the rubric to
match what you care about as a reviewer — it is the single place reviewers get
their rubric from.

## The `free` preset

`configs/opencode/oh-my-opencode-slim.json` gained a cross-provider free tier:

- orchestrator `opencode/big-pickle`
- oracle `opencode/big-pickle`
- librarian `google/gemini-3.5-flash-lite`
- explorer `openrouter/nvidia/nemotron-3.5-lightning:free`
- designer `google/gemini-3.8-flash`
- fixer `opencode/nemotron-3.5-lightning-free`

Fallback chains cross providers (zen → google → openrouter), so a rate-limited
free tier falls through to the next provider. Each provider consumes its own
key, which is why all three keys are set in CI secrets. Rate-limit realities:
OpenRouter caps `:free` models at 50 requests/day under $10 credits (1000/day
at or above, 20 RPM); Gemini limits are per-project (see AI Studio); Zen free
models have no published fixed limits (and contributor models carry privacy
caveats). Refresh procedure:
[`configs/skills/free-preset/SKILL.md`](../configs/skills/free-preset/SKILL.md)
and [docs/MODEL_UPDATES.md](MODEL_UPDATES.md).

## Cron extension pattern

The reusable workflow accepts an `agents` and `prompt` input, so scheduled
jobs can call it with arbitrary prompts. Scheduled workflows run with the
reusable workflow's declared permissions (`contents: read`); push-capable cron
runs would require permission changes and are a future extension, not a current
capability. A scheduled caller is a thin workflow:

```yaml
on:
  schedule:
    - cron: "0 12 * * 1"
jobs:
  weekly:
    uses: randytarampi/dotfiles/.github/workflows/agentic-review.yml@<immutable-40-hex-sha>
    with:
      agents: "opencode"
      trusted_ref: <same-immutable-40-hex-sha>
      prompt: "Weekly repository hygiene pass: stale branches, failing CI, dependency drift."
    secrets: inherit
```

## Known limitations

Reviewer jobs execute with default tool permissions. A stricter CI permission
profile is follow-up work; dispatcher gating is the primary control.

## Publication acceptance and known limitations

OpenCode's official action publishes its review through the installed App and
requires `id-token: write` on the caller. A successful native run is not the
same as an independent check of substantive review content: inspect the App's
comment and the exact reviewed PR head. It may create a separate comment on
repeat requests; unlike the former custom publisher it has no stable comment
marker/update-in-place contract. The native action may write PR branches when
using a mutating agent, hence this workflow's read-only plan configuration and
push guard. Test real events, permissions and lack of mutations.

Junie and Gemini require successful model execution, nonempty review artifacts
and a `notify` token authorized for issue comments. Failed models and missing
publication permissions must not produce misleading success. Copilot requests a
native review and gets no redundant status comment. Unrequested events do not
publish.

Before promotion, test a same-repository, maintainer-controlled PR with the
exact action ref: OIDC exchange, substantive review, publication, no code push,
and the supported comment triggers; verify label events skip OpenCode. Separately check failed model calls,
missing/ineligible App installation and unrequested events. The new action
controls its own comment lifecycle; do not claim old idempotency guarantees.

OpenCode checks out a PR merge ref. Junie and Gemini check out the PR head ref
and compare the checked-out SHA with the live PR head before model execution.
This guards against an incorrect starting snapshot; verify the head has not
changed before accepting the published review. Advancing the immutable
`trusted_ref` in dispatchers and re-onboarding downstream consumers is a
separate rollout step after merge and runtime validation.

## Predictability

Junie and Gemini normal review lanes are read-only: they do not commit, push, or alter the
checkout. Any write-capable automation is isolated behind an explicit workflow,
protected environment, exact path checks, and a GitHub App installation token.

## Copilot lane prerequisites

The Copilot lane only requests `copilot-pull-request-reviewer[bot]` via the
REST API — it does not (and cannot) enable Copilot review for a repo. The
mechanism is officially documented and works in eligible repositories (GitHub's
own `awesome-copilot` uses it), but it can return 200 while silently leaving
`requested_reviewers` empty. Verified causes, in order of likelihood:

1. **Plan gate** — Copilot Code review requires Copilot Pro, Pro+, or Max.
   **Copilot Free does not include code review** for personal repositories
   ("Copilot is enabled" is a different control from having code review).
2. Reviewer spelling — the raw REST field needs exactly
   `copilot-pull-request-reviewer[bot]` (not `@copilot`, `Copilot`, or the
   un-suffixed form, which succeed while dropping the reviewer).
3. Org policy/plan gates for organisation repositories; native triggers are
   reviewer assignment, PR labels, and in-PR comments — a plain `@copilot`
   issue comment is not a documented trigger.

Diagnostic: run the request with an eligible personal token
(`gh pr edit <pr> --add-reviewer @copilot`), then poll `requested_reviewers`
— a 200 with an empty result means eligibility, not the workflow.

## Local pre-push review

Run the same trusted prompt locally before pushing:

```sh
scripts/run-local-review.sh [--staged|--base <ref>] [--model <id>]
```

The default model is read from the `free` preset at runtime. The runner reviews
the working-tree diff against `HEAD` by default, supports staged or base-ref
diffs, and works with local Ollama through configured tier fallbacks. It never
pushes or mutates repository files.

The default model requires usable OpenCode authentication and configuration.
For an explicitly local review, use for example:
`scripts/run-local-review.sh --model ollama/qwen3.8:27b-mlx`.

## Onboard another repo

Run `scripts/onboard-agentic-review.py --repo <path> --ref <ref>` to install the
stable dispatcher stub and Copilot setup workflow. The helper is idempotent, creates backups
when replacing existing workflows (disable with `--no-backup`), and supports
`--dry-run` and `--workflows-only`. The shared prompt and skills are checked out
automatically from `randytarampi/dotfiles`; manually create secrets and labels,
then configure Copilot Settings → MCP servers as described above.

The supplied immutable SHA seeds the Copilot workflow's automatic default;
manual dispatch accepts an optional override.

> **Dispatcher recovery note:** The dispatcher is currently hand-unpinned: `uses:` tracks
> `@main`, while `trusted_ref:` is SHA-anchored to a recent main revision (check
> `.github/workflows/agent-review.yml` and
> `.github/workflows/copilot-setup-steps.yml` for the
> current values).
> The original pin captured `357760a` from the deleted `fix/review-trust-refactor` branch,
> making every dispatch fail with 0 jobs.
> The dispatcher intentionally uses the reusable workflow at `@main`; `trusted_ref`
> independently pins the trusted review assets to an immutable commit. Run
> `make anchor-review-ref` from the local `main` branch to fetch `origin/main` and
> update the dispatcher `trusted_ref` and Copilot setup's trusted SHA default
> and automatic-run fallback together. The fetched commit must be reachable from
> local `main`; fetch/ancestry failures stop without changing either workflow.
> This deliberately does not require the fetched commit to equal `HEAD`, since
> the anchoring change itself advances `HEAD`. Use `make anchor-review-ref DRY_RUN=1` to preview; dry-run
> does not fetch or modify Git refs and fails with guidance if the remote commit is
> not already available locally. The
> helper never changes the `uses: ...@main` dispatcher pin; do not use the
> onboarding generator to update this repository's dispatcher.

## What this does not cover

- PR-Agent (optional fourth reviewer) — add later as another job on the same
  labels if wanted.
- Distribution to the other repos — copy `agent-review.yml` there and point
  `uses:` at this repo; secrets must be set per repo.
- OpenCode App installation is required for the OIDC action; downstream repos
  must install the app and grant their dispatcher `id-token: write`.

### Official OpenCode action prompt compatibility

At the inspected upstream action commit, `opencode github run` reads the
original GitHub comment body; the composite action advertises a `prompt`
input but its CLI implementation does not consume that value. Therefore the
native OpenCode lane **does not claim automatic enforcement of the trusted
review rubric**. The repo's `AGENTS.md` remains available during review, and
maintainers can include review-specific instructions in an authorized
`/opencode` comment. Restoring strict canonical-rubric injection into the
native action is separate work that requires upstream-supported behavior.

### Native OpenCode checkout credential compatibility

The upstream action expects an existing Git HTTP authentication header and
replaces it with its own short-lived App installation token. Persisting the
checkout's `github.token` caused **duplicate Authorization headers** during
the native action's PR branch fetch (live run 40100986283, attempt 2).
The OpenCode checkout now keeps `persist-credentials: false`. A non-secret
placeholder header is staged immediately before the native action, replaced by
the action's App token, and removed in an `always()` cleanup step. No
`github.token` value is stored in the placeholder. The other reviewer lanes
retain their previous checkout authentication policy.

### Independent reviewer requests and Junie checkout

Separate review-request comments no longer cancel each other merely because
both target one PR. Workflow concurrency uses the triggering comment's unique
ID (or run identity for non-comment triggers). Junie and Gemini check out and
verify the requested PR head, rather than the default branch or PR merge ref.
A reviewer run succeeds only with actual output and an authenticated publisher;
no-op dispatcher runs are not evidence of review publication.


## October 24, 2026: Junie/Gemini publication verification

- [Junie smoke 40104437396](https://github.com/randytarampi/dotfiles/actions/runs/40104437396) produced a substantive review but publishing failed closed because no GitHub App publication credentials were configured.
- [Gemini smoke 40104850565](https://github.com/randytarampi/dotfiles/actions/runs/40104850565) failed with repeated 503 errors and exhausted its 20-request free daily quota for `gemini-3.8-flash`; its summary was empty. A completed output-artifact upload is not evidence of review generation.
- The notification job now mints scoped Junie/Gemini App installation tokens and attributes its comments to the actual returned App slugs. The previously verified `github-actions[bot]` attribution applies only to the older publisher smoke tests, not the new dedicated App path. Junie/Gemini only receive `contents: read`. Native Junie publishing remains disabled: disabling `silent_mode` would enable upstream commit/push pathways.
- Both reviewers check out the requested PR **head** ref and verify the exact HEAD SHA through the GitHub API before model execution. Publisher updates only its own per-reviewer marker comments. API lookup errors, failed models, whitespace-only bodies and missing tokens fail visibly; `notify` publishes no partial results when a requested reviewer fails. Check that the PR head does not move afterward.
- Both disposable [PR #98](https://github.com/randytarampi/dotfiles/pull/98) and [PR #102](https://github.com/randytarampi/dotfiles/pull/102) are closed. On #102 the candidate publisher passed end-to-end: [run 40117338557](https://github.com/randytarampi/dotfiles/actions/runs/40117338557) generated and posted a head-specific [Junie review](https://github.com/randytarampi/dotfiles/pull/102#issuecomment-6387465307). A second authorized request ([run 40117497312](https://github.com/randytarampi/dotfiles/actions/runs/40117497312)) updated that same comment ID, with exactly one publisher-owned Junie marker comment and an unchanged PR head. The production dispatcher was restored to `@main` after every temporary pin. This proves the GitHub Actions bot publication path, not native JetBrains App identity; Gemini remains unverified.


### Live permission failure and follow-up

The first Junie candidate [run 40115982101](https://github.com/randytarampi/dotfiles/actions/runs/40115982101) generated a substantive review but `notify` received HTTP 403 `Resource not accessible by integration` when posting with an effective `Issues: write` token. With `pull-requests: write` instead, live [run 40117338557](https://github.com/randytarampi/dotfiles/actions/runs/40117338557) successfully published as `github-actions[bot]` and [run 40117497312](https://github.com/randytarampi/dotfiles/actions/runs/40117497312) updated the [same comment](https://github.com/randytarampi/dotfiles/pull/102#issuecomment-6387465307). Neither model generation job has GitHub write permissions. Gemini's equivalent end-to-end proof was explicitly deferred by the maintainer to [issue #106](https://github.com/randytarampi/dotfiles/issues/106); do not represent Gemini as fully verified. Exact-head CI/CodeQL are required before merge.


### GitHub App cutover (2026-10-24)

The review jobs still use `silent_mode: true` for Junie and explicit `notify` publication for Gemini; switching to App authentication does not turn on native code editing, pushes, or vendor-native publication. `notify` mints a token with `actions/create-github-app-token@v3` from each agent's `*_GITHUB_APP_ID` variable and `*_GITHUB_APP_PRIVATE_KEY` secret only when that reviewer succeeds. Its exact returned `app-slug` determines marker ownership. A pre-existing `github-actions[bot]` marker must be deliberately retired by the maintainer before a new bot identity can own that marker; a foreign marker makes publication fail rather than post a duplicate. The OpenCode native OIDC integration stays unchanged; its newly configured App credentials are not needed for this path.

The previously observed Junie publication and repeat-update [on PR #102](https://github.com/randytarampi/dotfiles/pull/102#issuecomment-6387465307) prove the older `github-actions[bot]` publisher, not the new dedicated App publisher. Dedicated Junie App identity and repeat-update succeeded on [PR #105](https://github.com/randytarampi/dotfiles/pull/105#issuecomment-6388327089) ([repeat run 40125515479](https://github.com/randytarampi/dotfiles/actions/runs/40125515479)). Gemini model execution remains unverified due to 503/429 quota failures; its end-to-end acceptance was owner-deferred to [issue #106](https://github.com/randytarampi/dotfiles/issues/106).


### Final handoff — dedicated publisher Apps

The dedicated Junie App `junie-randytarampi-ca[bot]` published the head-specific [review on PR #105](https://github.com/randytarampi/dotfiles/pull/105#issuecomment-6388327089) in [run 40123686460](https://github.com/randytarampi/dotfiles/actions/runs/40123686460). [Run 40125515479](https://github.com/randytarampi/dotfiles/actions/runs/40125515479) updated the same comment without duplication. The fixture's head did not change. [Gemini run 40125520755](https://github.com/randytarampi/dotfiles/actions/runs/40125520755) failed during generation with HTTP 503 and free-tier quota 429, and `notify` failed closed. The maintainer explicitly accepted deferring Gemini's **actual model and App comment publication** verification to [issue #106](https://github.com/randytarampi/dotfiles/issues/106). The configured GitHub App publishing path is therefore implemented, but Gemini is not independently proven. The stable dispatcher is back on `@main`. Review security and tests must still pass the final PR head checks.

## Phase II integration retrospective (2026-10-31)

The lessons below complement, rather than override, `AGENTS.md`, the shared
agent guidance, and the PR template.

- **Define observable acceptance before coding.** Name the actual client and
  protocol, expected effective configuration, owner-held state, side effects,
  negative cases, and evidence needed to call a transport usable. Installing an
  app, generating a key and running a real inference task are separate milestones.
- **Keep a single integration steward.** Shared client registries, key writers,
  gate names, environment templates and doctor rules must change as one contract.
  Rebase each narrow PR after its predecessor lands; test the combined state.
- **Test the user entrypoint.** A successful helper unit test does not prove a
  `make` target, generated LaunchAgent, GUI environment, or real client reads it.
  Isolate `HOME` and `PATH`; test malformed input, absent installation, gate-off
  conditions, symlink/permissions failures, no-touch refusal and idempotence.
- **Make evidence labels explicit.** Record targeted tests, full `make verify`,
  exact-head required CI, Qlty, owner-reported deployment, and independently
  observed live gateway requests separately. Do not upgrade UNKNOWN to PASS.
- **Apply a stop budget.** After two failed repair attempts in the same subsystem,
  inspect logs, reset the test seam or ask for a bounded scope decision rather
  than stacking speculative commits. Run the project formatter and executable
  contract before pushing a patch that changes a public script.
- **Preserve user-owned state.** Prefer dry-run, private backup, compare-and-swap
  and explicit refusal to destructive reconciliation; verify real producer to
  consumer readback and deploy twice only with authorization.
- **Close review findings deliberately.** Mark each as fixed with decisive
  evidence, owner-declined, tracked follow-up, advisory or still open. A green
  bot check is not a substitute for this review ledger.
