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
| OpenCode | `anomalyco/opencode/github` | `/oc <prompt>` or `/opencode <prompt>` mention, or `review-opencode` label | `OPENCODE_API_KEY` | local preset roles via explicit model + provider blocks, MCP mirror, skills, codegraph |
| Junie | `JetBrains/junie-github-action@v1` | `@junie-agent <prompt>` mention, or `review-junie` label | `JUNIE_API_KEY` | shared review method (custom-prompt mode with GitHub context attached) |
| Gemini | `google-github-actions/run-gemini-cli@v0` | `@gemini-cli /review` mention, or `review-gemini` label | `GEMINI_API_KEY` | behavior regressions, missing tests, operational risk |
| Copilot auto-request | REST job in the reusable workflow | `review-copilot` label | none | requests `copilot-pull-request-reviewer[bot]` |

`review-all` fans out to opencode, junie, gemini, and copilot. Mentions work
regardless of labels; labels gate unprompted reviews. OpenCode is label-gated
like the others.

## Labels

- `review-opencode` — OpenCode agentic review
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
  the new ref and matching `trusted_ref` together.
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
  It installs the stable stub and writes the same immutable SHA into both the
  reusable workflow `uses:` pin and `trusted_ref`; mutable refs such as `@main`
  are not accepted.
  Re-onboarding is required whenever the trusted workflow revision advances,
  as well as when trigger events or permissions change.
  The CI OpenCode lane runs the explicitly selected model with all three
  provider keys available. The repo's local fallback policy is a runtime
  plugin concern and is not part of the CI config.
  OpenCode, Junie, and Gemini stage the trusted dotfiles commit identified by
  the caller-provided immutable `trusted_ref` and verify its asset manifest
  before using prompts, configuration, skills, or `ci-codegraph.sh`.
  Copilot setup accepts an explicit 40-character dotfiles commit SHA for the
  same reason; PR content never participates in prompt-file loading.
- Security posture: minimal `permissions` per job, read-only `github.token` in
  every agent lane, `sender.type != 'Bot'`
  filter, per-PR `concurrency` cancel-in-progress, and verified floating-major
  refs for third-party actions,
  read-only MCP tool allowlists, no `pull_request_target`. Normal OpenCode,
  Junie, and Gemini lanes have only `contents: read`, do not persist checkout
  credentials, and cannot publish. Each lane hands its review body to `notify`
  as a job output and review artifact; `notify` is the only job that publishes
  PR comments, using only a narrowly scoped GitHub App token. The separate manual fix lane is
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
(`opencode-ai@1.18.33`) and verifies both its executable and reported version;
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

- Per-agent GitHub Apps: `JUNIE_APP_ID` + `JUNIE_APP_PRIVATE_KEY`,
  `OPENCODE_APP_ID` + `OPENCODE_APP_PRIVATE_KEY`, and `GEMINI_APP_ID` +
  `GEMINI_APP_PRIVATE_KEY`. These mirror the manual App setup described by
  `anthropics/claude-code-action` Option 2 and post as `<app-slug>[bot]`.
- Fallback GitHub App: `APP_ID` + `APP_PRIVATE_KEY`. When a per-agent pair is
  absent, this App is used for that lane.
- App tokens are minted per notify job with a one-hour expiry. Each App must be
  installed on the consuming repository with Issues: write and Pull requests:
  write permissions. If no per-agent or fallback App is configured, `notify`
  skips publication; it never falls back to `github.token` or
  `github-actions[bot]`.
- OpenCode runs the version-pinned CLI directly with
  `opencode run --print-logs --format default` and uploads its captured review.
  The pinned GitHub action was not used because its `opencode github run`
  integration has no output channel and can publish internally. The pinned
  Junie action exposes the documented `silent_mode: "true"` input and
  `junie_summary` output; that mode is enabled so Junie emits its summary
  without attempting its normal feedback comments. Both summaries are
  consumed by `notify`, which publishes the actual bodies.
  A poster account should not manually issue trigger comments: unlike
  `github-actions[bot]`, a PAT-backed user is not filtered as a bot and could
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
- explorer `openrouter/inclusionai/ling-3.0-flash-sante:free`
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

## Predictability

All normal reviewer lanes are read-only: they do not commit, push, or alter the
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

> **Dispatcher recovery note:** The dispatcher is currently hand-unpinned: `uses:` tracks
> `@main`, while `trusted_ref:` is SHA-anchored to a recent main revision (check
> `.github/workflows/agent-review.yml` for the current value).
> The original pin captured `357760a` from the deleted `fix/review-trust-refactor` branch,
> making every dispatch fail with 0 jobs.
> The dispatcher intentionally uses the reusable workflow at `@main`; `trusted_ref`
> independently pins the trusted review assets to an immutable commit. Run
> `make anchor-review-ref` from the local `main` branch to fetch `origin/main` and
> update only `trusted_ref`. The fetched commit must be reachable from local `main`;
> fetch/ancestry failures stop without changing the workflow. This deliberately
> does not require the fetched commit to equal `HEAD`, since the anchoring change
> itself advances `HEAD`. Use `make anchor-review-ref DRY_RUN=1` to preview; dry-run
> does not fetch or modify Git refs and fails with guidance if the remote commit is
> not already available locally. The
> helper never changes the `uses: ...@main` dispatcher pin; do not use the
> onboarding generator to update this repository's dispatcher.

## What this does not cover

- PR-Agent (optional fourth reviewer) — add later as another job on the same
  labels if wanted.
- Distribution to the other repos — copy `agent-review.yml` there and point
  `uses:` at this repo; secrets must be set per repo.
- GitHub App installation for the OpenCode action (OIDC mode) — the token
  approach (`github.token`) is used; switch if App-based auth is ever needed.
