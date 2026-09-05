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

- `.github/workflows/agent-review.yml` — dispatcher for this repo. Listens for
  `issue_comment` / `pull_request_review_comment` (mention triggers) and
  `pull_request[labeled]` (label triggers). Filters bot senders, parses the
  request with `jq` into workflow outputs (comment content is never
  interpolated into shell), and calls the reusable workflow with
  `secrets: inherit`.
- `.github/workflows/agentic-review.yml` — reusable `workflow_call` workflow.
  Downstream repos call it with:

  ```yaml
  jobs:
    review:
      uses: randytarampi/dotfiles/.github/workflows/agentic-review.yml@main
      with:
        agents: "opencode,junie,gemini,copilot"
      secrets: inherit
  ```

  To adopt elsewhere, copy `agent-review.yml` and point its `uses:` at
  `randytarampi/dotfiles@main` (or pin a release tag for reproducibility).
  The CI OpenCode lane runs the explicitly selected model with all three
  provider keys available. The repo's local fallback policy is a runtime
  plugin concern and is not part of the CI config.
  OpenCode and Gemini load the shared prompt from a trusted
  `randytarampi/dotfiles@main` checkout using a random environment delimiter;
  PR content never participates in prompt-file loading.
- Security posture: minimal `permissions` per job, `sender.type != 'Bot'`
  filter, per-PR `concurrency` cancel-in-progress, actions pinned to moving
  major tags (OpenCode pinned to its release SHA — it publishes no major tag),
  `persist-credentials: false`, read-only MCP tool allowlists, no
  `pull_request_target`.

## Secrets

Set per repo (Settings → Secrets and variables → Actions):

- `OPENCODE_API_KEY` — OpenCode Zen (used by the OpenCode job and its
  cross-provider fallbacks)
- `JUNIE_API_KEY` — Junie backend token (from junie.jetbrains.com/cli)
- `GEMINI_API_KEY` — Google AI Studio
- `OPENROUTER_API_KEY` — OpenRouter (consumed by OpenCode's `free`
  fallback chains in CI; also usable by Junie BYOK if preferred)

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
reusable workflow checks out trusted assets from `randytarampi/dotfiles@main`
and copies the skill into `.opencode/skills/` at runtime (`.opencode/` is
gitignored).
Copilot reads equivalent guidance from repo instructions. Tweak the rubric to
match what you care about as a reviewer — it is the single place reviewers get
their rubric from.

## The `free` preset

`configs/opencode/oh-my-opencode-slim.json` gained a cross-provider free tier:

- orchestrator `opencode/muse-spark-1.3-contributor-free`
- oracle `opencode/big-pickle`
- librarian `google/gemini-3.5-flash-lite`
- explorer `openrouter/cohere/north-mini-code:free`
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
    uses: randytarampi/dotfiles/.github/workflows/agentic-review.yml@main
    with:
      agents: "opencode"
      prompt: "Weekly repository hygiene pass: stale branches, failing CI, dependency drift."
    secrets: inherit
```

## Known limitations

Reviewer jobs execute with default tool permissions. A stricter CI permission
profile is follow-up work; dispatcher gating is the primary control.

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

Run `scripts/onboard-agentic-review.py --repo <path> [--ref <ref>]` to copy the
dispatcher and Copilot setup workflow. The helper is idempotent, creates backups
when replacing existing workflows (disable with `--no-backup`), and supports
`--dry-run` and `--workflows-only`. The shared prompt and skills are checked out
automatically from `randytarampi/dotfiles`; manually create secrets and labels,
then configure Copilot Settings → MCP servers as described above.

## What this does not cover

- PR-Agent (optional fourth reviewer) — add later as another job on the same
  labels if wanted.
- Distribution to the other repos — copy `agent-review.yml` there and point
  `uses:` at this repo; secrets must be set per repo.
- GitHub App installation for the OpenCode action (OIDC mode) — the token
  approach (`github.token`) is used; switch if App-based auth is ever needed.
