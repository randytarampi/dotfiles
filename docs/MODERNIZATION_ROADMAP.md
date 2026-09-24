# Dotfiles modernization roadmap

> **Status:** Productionalization roadmap, reconciled 2026-09-22
> **Scope:** dotfiles productionalization, portability, local-model operations, governance and documentation
> **Confidence:** `[verified]` is supported by repository files or a recorded check; `[believed]` is a bounded interpretation; `[aspirational]` is a target.

## 1. Document contract

This is the durable plan for deferred work from the productionalization
session. It is self-contained: every claim cites repository files or a
recorded check, and durable documentation never depends on private session
state.
The roadmap does not authorize changes to tier configuration, package policy,
secrets, or CI gates without a separate approved work item.

Effort estimates are relative implementation estimates: **S** is hours,
**M** is one to two focused days, **L** is several days, and **XL** is a
multi-session change. They are estimates, not recorded durations.

## 2. Baseline and completed work

- `[verified]` Phase 1 through Phase 4 are complete at `128125b`; rounds 1–2 of
  follow-up work landed through `d0d5090`. The recorded validation is
  `make verify`, `make test` (160+ tests), `make ci-verify`, `poetry check` and
  actionlint; the production coverage measurement is 36% (branch-aware pytest;
  Coveralls reports 33.71% line coverage at `9d05d65`) against a floor now
  ratcheted to 27% (evidence: [pyproject.toml](../pyproject.toml) and CI run
  35713182137, all-green including the required Windows lane).
- `[verified]` The Windows deploy lane was **promoted to required** at
  `288b410` after three consecutive green runs (`5e48340`, `389ab47`,
  `f237cb6`); `continue-on-error` is removed from the deploy matrix.
  [`.github/workflows/ci.yml`] The nightly lane runs OPENCODE, MCP and
  AGENT_GUIDANCE with postconditions and remains allow-failure.
  [.github/workflows/nightly-integration.yml]
- `[completed-in-this-pass]` The orchestration range now names the actual
  `run_onchange_04` through `run_onchange_29` scripts, and its inventory includes
  `install-acp-adapters`. The `wingetfile.dev` cross-references now use the
  corresponding Brewfile identifiers. [docs/ORCHESTRATION.md:62-66,165-198;
  wingetfile.dev:1-38]
- `[verified]` 2026-09-22 — dated coverage-semantics correction. Three numbers
  circulated for the same floor: 32% (roadmap, superseded), 33% (branch-aware
  pytest at `9d05d65`), 33.71% (Coveralls line coverage, build 81894625).
  The authoritative internal gate is the branch-aware pytest measure; Coveralls
  line coverage is the external report. The floor is 27%.
  [pyproject.toml; CI run 35713182137]

| Item | Status | Evidence |
|---|---|---|
| Licence | `[completed-in-this-pass]` | Unlicense (public-domain dedication) is recorded in [`LICENSE`](../LICENSE) and linked from the README badge. |
| qlty | `[completed-in-this-pass]` — integrated 2026-09-22 | The earlier "no credential-free way to verify a qlty Cloud project" claim was **disproven** (me/'s OIDC pattern), and the follow-up work item has now landed: minimal [qlty.toml](../qlty.toml) (release checks opted out) plus a supplemental coverage upload in [ci.yml](../.github/workflows/ci.yml), isolated from the required gate (step-level `continue-on-error`, success-gated). Coveralls remains the primary report by design. |
| Action-SHA pinning | `[completed-in-this-pass]` — policy recorded | Required/security/deployment workflows (ci, nightly, codeql) pin verified immutable full SHAs; write-capable agent review workflows float by documented design ([docs/AGENTIC-REVIEW.md](AGENTIC-REVIEW.md): moving major tags) and remain an **enumerated gap**, not a settled exception. No repo-wide `sha_pinning_required` is proposed while those lanes float. |

## 3. Ordered work items

### 1. Close the cross-platform deploy probe

**Priority:** High · **Effort:** L · **Dependencies:** Phase 4 Windows failures,
the audit inventory below, and a reproducible Windows runner.

Fix only failures demonstrated by the Windows deploy lane. The audit identifies
`readlink -f`, `mapfile`, Homebrew paths, LaunchAgents, `launchctl`, and
platform-specific doctor checks; it is not permission for a broad rewrite
(see the shell-to-Python inventory below for script-level evidence).

**Status:** `[completed-in-this-pass]` The Windows lane was **promoted to
required** at `288b410` after three consecutive green runs (`5e48340`,
`389ab47`, `f237cb6`); `continue-on-error` is removed from the deploy matrix,
and every promotion fix was demonstrated and bounded (script-exec interpreters
chain, cygpath forward-slash, deploy-effective no-op check).
[.github/workflows/ci.yml; runs 35713182137, 35696345590]
**Residual (deliberate):** `make doctor` remains Unix-gated in CI
  ([.github/workflows/ci.yml:184-189]); making doctor meaningful on Windows is
the next Windows extension, backlogged.

**Acceptance criteria:** the Windows lane has a documented result for every
failure; bounded fixes pass the Windows deploy, second-deploy idempotency and
chezmoi verification checks; residual macOS-only paths are explicitly gated;
and `continue-on-error` is removed once the lane is stable and required.

### 2. Promote the nightly real-gate lane

**Priority:** High · **Effort:** L · **Dependencies:** item 1 stability, pinned
prerequisites and per-gate artefacts.

Keep the scheduled lane allow-failure while it proves meaningful postconditions.
Package-install families are intentionally absent today: PI, MOZART and
CODEGRAPH need approved, pinned prerequisites rather than warning-and-return-zero
behaviour. [.github/workflows/nightly-integration.yml]

**Acceptance criteria:** each enabled gate has an installed prerequisite,
deterministic artefacts and a failure assertion; service, security and system
default gates remain excluded from shared runners; the report distinguishes
skipped, failed and passed gates; then `continue-on-error` is removed and the
workflow is made required after an observed stabilization period.

**Status:** `[in-progress]` Promotion semantics corrected 2026-09-22: this is a
schedule-only workflow, so it can **never be a required pull-request check**.
Promotion means removing `continue-on-error` so scheduled failures are truthful,
with failure notification/issue ownership. Decision date: on or after
2026-09-29, gated on roughly seven consecutive green scheduled runs on the
current logic (only one scheduled + one manual success at `5e48340` exists so
far). [.github/workflows/nightly-integration.yml]

### 3. Add oMLX audio-upload validation and normalization

**Priority:** High · **Effort:** M · **Dependencies:** the existing
`configure-omlx` scripts and the oMLX service restart path.

The machine-side `~/.omlx/settings.json` value
`server.max_audio_upload_size` was the unitless string `128`, meaning 128 bytes;
it caused STT HTTP 413 responses. It was corrected machine-side to `128MB`,
after which the transcription endpoint returned HTTP 200. This was true before
this pass; the value is now validated by the configure and doctor paths below.

**Status:** `[completed-in-this-pass]` The validator exists and is tested
([scripts/lib/local_engines.py](../scripts/lib/local_engines.py):
`_max_audio_upload_size`, wired into `merge_omlx_settings` with an
`OMLX_MAX_AUDIO_UPLOAD_SIZE` override; doctor check in
  [scripts/verify-config.py](../scripts/verify-config.py); seven hermetic cases in
[scripts/lib/tests/test_omlx_settings.py](../scripts/lib/tests/test_omlx_settings.py)).
Decided semantics (2026-09-22): unitless values are **refused at write time**
(bytes-accurate; no MB reinterpretation — the `'128'`-bytes incident is
encoded), invalid values warn-and-preserve rather than abort the deploy, and
`validate_omlx_settings` remains the hard structural gate. [docs/OMLX.md]
**Residual (open):** no pre-STT runtime effective-limit check exists; the
acceptance criterion "verifies the effective limit before STT use" is not met
and stays open rather than being silently dropped.

### 4. Ratchet production coverage modestly

**Priority:** Medium · **Effort:** M per quarter · **Dependencies:** the
measured production baseline and subprocess coverage already in place.

The production floor was 27%; the measured result is 36% (branch-aware pytest;
Coveralls reports 33.71% line coverage). On 2026-09-22 the floor was ratcheted
  to **27%** ([pyproject.toml](../pyproject.toml)), keeping ~9 points of headroom
so one flaky test cannot break `main`; 30+ is gated on representative tests
(orchestration, failed/partial deploy paths), not wrapper-test padding.
[pyproject.toml; Makefile test target] Raise the floor in small
quarterly increments only when new coverage is representative, rather than
targeting 100%.

**Acceptance criteria:** each ratchet records the measured production baseline,
the new threshold and the tests that justify it; subprocess and shell behaviour
remain included; and `make verify` fails below the ratchet without counting test
files as production coverage.

### 5. Port only proven shell candidates to Python

**Priority:** High for the first group, otherwise Medium · **Effort:** M per
script · **Dependencies:** a failing or materially constrained Windows probe;
CLI capability contracts and regression tests.

Apply the criteria **logic-heavy, weakly tested and platform-sensitive** to the
Phase 4 inventory. These are quality-driven port candidates, not Windows-
necessary work: Git Bash resolved the Windows probe without ports.
**Status:** `[completed-in-this-pass]` Tranche 1 (the first three candidates)
is delivered with parity tests and thin compatibility shims:
[scripts/setup-bin-symlinks.py](../scripts/setup-bin-symlinks.py) +
[scripts/lib/tests/test_setup_bin_symlinks.py](../scripts/lib/tests/test_setup_bin_symlinks.py),
[scripts/update-nvm-globals.py](../scripts/update-nvm-globals.py) (single-shell
nvm flow with structured markers, after a post-review fix),
[scripts/install-acp-adapters.py](../scripts/install-acp-adapters.py).
Tranche 2 stays **evidence-gated**: `run-local-review.sh` ports only on a
demonstrated Bash/path failure or materially untestable change;
`configure-all.sh` is high-risk (sourced-library semantics) and is not ported
speculatively.

| Order | Candidate | Evidence and bounded acceptance |
|---|---|---|
| Tranche 1 · 1 | `scripts/setup-bin-symlinks.sh` | **Done** — Python port + parity tests + thin `.sh` shim; wired by `run_onchange_05`. |
| Tranche 1 · 2 | `scripts/update-nvm-globals.sh` | **Done** — Python port + parity tests; wired by `update-system.sh`. |
| Tranche 1 · 3 | `scripts/install-acp-adapters.py` | **Done** — Python port + parity tests; consumed directly by `run_onchange_10` (the former `.sh` wrapper was deleted in `af1c1f5`). |
| Tranche 2 · 4 | `scripts/run-local-review.sh` (`readlink -f`, `mapfile`) | Deferred pending evidence. Preserve review stages and exit statuses; add a fixture for the Bash-version-sensitive input path. [scripts/run-local-review.sh] |
| Tranche 2 · 5 | `scripts/configure-all.sh` (`readlink -f`) | Deferred pending evidence; design the interaction with sourced `common.sh`, `tier_args.sh` and `env.sh` before porting. [scripts/configure-all.sh; scripts/lib/common.sh; scripts/lib/tier_args.sh; scripts/lib/env.sh] |

**Acceptance criteria for every port:** the shell implementation is not removed
until the Python replacement has parity tests, `--help`/dry-run behaviour where
applicable, platform evidence and a clean `make verify`; macOS-bound scripts
remain shell when a port would only disguise an operating-system boundary.

### 6. Decide the audited tool configuration boundary

**Priority:** High for the first four tools, Medium for the next eight, Low for
the final seven · **Effort:** M · **Dependencies:** package ownership and
machine-specific credentials.

The following is analysis and recommendation only. Each tool must either gain a
minimal, gated configuration surface or be documented as intentionally absent;
no credentials are added by this roadmap. Two states are distinguished:
**already packaged, not configured** (package entries exist in a Brewfile or
wingetfile but no gate generates configuration) versus **package missing**
(no package entry anywhere).

| Priority | Tool | Why it matters | Recommended action |
|---|---|---|---|
| High | Docker | Package and deploy paths expect Docker-compatible local workflows and defaults. | `[completed-in-this-pass]` Gated, non-secret `~/.docker/config.json` baseline shipped: [scripts/configure-docker.py](../scripts/configure-docker.py) (`DOTFILES_RUN_DOCKER_CONFIG_SETUP`, create-only-if-missing, JSON parse-verify of existing, malformed → error-not-clobber), wired into `configure-all.sh` after the AWS step, documented in `.env.example` + [docs/ORCHESTRATION.md](ORCHESTRATION.md), five hermetic tests incl. the credsStore-absent case (credential helper detection). |
| High | AWS CLI | AWS profiles are an expected operator boundary for cloud scripts; credentials remain user-owned. | `[completed-in-this-pass]` Gated, non-secret `~/.aws/config` baseline shipped: [scripts/configure-aws.py](../scripts/configure-aws.py) (`DOTFILES_RUN_AWS_CONFIG_SETUP`, create-only-if-missing, configparser verify of existing), wired into `configure-all.sh`, documented in `.env.example` + [docs/ORCHESTRATION.md](ORCHESTRATION.md), four hermetic tests in [scripts/lib/tests/test_configure_aws.py](../scripts/lib/tests/test_configure_aws.py). |
| High | kubectl | Kubernetes operations need an explicit CLI and opt-in kubeconfig handling. | Package missing: add package coverage and a gated, non-secret kubeconfig/default-context policy; never generate credentials. Deferred until a real workload exists. |
| High | Pulumi | Infrastructure work needs backend and organisation defaults, while state and secrets are sensitive. | **Scope corrected 2026-09-22:** configure the Pulumi *CLI* if a dotfiles surface needs it, but never set a repository-level default backend/org — `me` owns its backend (`me/infrastructure/Pulumi.yaml`), and a dotfiles default would create a second owner. Decision deferred to the `me` governance programme. |
| Medium | gcloud | Cloud tooling is useful but no active repo workload currently requires a generated profile. | Package missing: add package coverage only if a gate-backed workload is identified; otherwise document intentionally absent. |
| Medium | mongosh / Compass | MongoDB is partially covered through skills and MCP; the CLI/app boundary is not configured. | Add package coverage and optional shell defaults only for an approved MongoDB workflow; otherwise document the partial coverage. |
| Medium | psql | Database troubleshooting benefits from a client and `.psqlrc`, but no current gate requires it. | Add package coverage and a small gated `.psqlrc` when a supported workflow exists; otherwise document absent. |
| Medium | Snyk | Security tooling is a possible policy gate, but no current verification target invokes it. | Document intentionally absent until a repository gate and authentication-free policy path exist. |
| Medium | Stripe CLI | Stripe development uses a CLI where local webhook workflows are enabled. | Already packaged, not configured (`Brewfile.dev` `stripe-cli`, `wingetfile.dev` `Stripe.StripeCli`): document login/webhook setup; add a gate only with a testable local workflow. |
| Medium | 1Password CLI | Password-manager access is sensitive and must not become an implicit deploy dependency. | Document intentionally absent from unattended gates; provide human-run installation guidance only if needed. |
| Medium | ClamAV | Malware scanning is service- and signature-update-dependent, so a package alone is not a truthful gate. | Document intentionally absent from shared CI; add an opt-in service/freshclam configuration only with an owner. |
| Medium | pyenv | Python version selection affects the scripts and cross-platform setup. | Add package coverage and shell hooks only if Poetry/system Python cannot satisfy the supported matrix. |
| Low | rbenv | No current repository gate requires Ruby version management. | Document intentionally absent. |
| Low | tfenv | Pulumi is the identified infrastructure tool; no Terraform gate is recorded. | Document intentionally absent unless a Terraform workload is approved. |
| Low | fastfetch | It is a convenience utility, not a verification dependency. | Keep package entries where already present; do not add configuration or gates. |
| Low | emacs | No orchestration or verification path expects Emacs. | Document intentionally absent. |
| Low | glances / htop / nvtop | Monitoring utilities are useful interactively but have no repo-owned gate. | Document intentionally absent from automation; users may install locally. |
| Low | ffuf | No current security workflow invokes web fuzzing. | Document intentionally absent until an owned, gated workflow exists. |
| Low | heroku | No active deployment path targets Heroku. | Document intentionally absent. |

The inventory derives from the unconfigured-tools audit and its stated partial
AWS/MongoDB/skills coverage, cross-checked against the package manifests
([Brewfile](../Brewfile), [Brewfile.dev](../Brewfile.dev),
[Brewfile.desktop.dev](../Brewfile.desktop.dev),
[Brewfile.dev.ops](../Brewfile.dev.ops), [wingetfile.dev](../wingetfile.dev),
[wingetfile.dev.ops](../wingetfile.dev.ops)).

### 7. Refresh council policy — completed in this pass

**Priority:** completed · **Effort:** — · **Dependencies:** resolved.

The model policy was decided and implemented in the same session at
`7030950`: `pro` keeps an Ollama Cloud-only council; `pro-plus` mixes Ollama
Cloud and OpenAI; `pro-plus-anthropic` adds Anthropic; `nemotron-3-ultra` was
replaced by `deepseek-v4.1-flash` (diversity over the marginally higher
`glm-5.3-flash` aggregate score); synthesizers stay `glm-5.3` (max) on all
three tiers. Tier docs, registry tests and configuration were updated together
([docs/TIERS.md](TIERS.md), `oh-my-opencode-slim.json`,
`scripts/lib/tests/test_tier_registry.py`).

### 8. Governance closure: aggregate checks, branch protection and merge flow — NEW 2026-09-22

**Priority:** High · **Effort:** M · **Dependencies:** the stable aggregate
checks delivered in this pass ([.github/workflows/ci.yml](../.github/workflows/ci.yml)
`ci/required`; [.github/workflows/codeql.yml](../.github/workflows/codeql.yml)
`security/required`); a fresh pull request observing the exact emitted check
context names.

This repository serves privileged workflows to six downstream repositories
(`.github/workflows/agentic-review.yml@main`, consumed with
`secrets: inherit` and write permissions), so changes to `main` are
fleet-significant and deserve staged governance.

**Staged model (target: a governed trunk, not ceremonial branch-and-merge):**

1. `[completed-in-this-pass]` Stable aggregates `ci/required` (gates on
    verify, the deploy matrix including the required Windows lane, and Coveralls
     finalization) and `security/required` (both CodeQL legs). The aggregates
     always run (`if: always()`) and gate explicitly on every predecessor's
     result — unlike `finish`, whose `always()` run asserts nothing. An
     unconditional `always()` job that asserts nothing is disqualified as a
     required check.
    [Makefile: `check-ci-assets` also added to `ci-verify` so asset drift
    cannot ship silently.]
2. `[completed-in-this-pass]` History protection on `main`: block deletion and
    non-fast-forward; bypass actor = repository admin, bypass mode `always`
    (the pattern proven in `me/infrastructure/src/github/rulesets.ts:30-32`).
    Zero workflow prerequisites; safe regardless of push flow; imports cleanly
    into the Pulumi governance stack later.
3. `[completed-in-this-pass 2026-09-22]` Required-check enforcement: the
    exact check context names were observed on PR #7 (`ci/required`,
    `security/required`) and the ruleset now requires them (ruleset id
    23837328, active): pull requests required before merge on
    `refs/heads/main`, status checks `ci/required` + `security/required`
    mandatory, admin bypass retained as break-glass only. Zero approving
    reviews (a solo operator cannot independently approve); agent review
    workflows are never required checks; direct push to `main` is no longer
    the default flow — use PRs, with admin break-glass documented in the
    decision record below.
4. `[completed-in-this-pass]` Dependabot vulnerability alerts and security
    fixes enabled (version updates already configured; auto-merge stays off
    until the dependency policy lands).

**Decision record (2026-09-22, final):** the pilot proved the aggregates and
the PR-first flow on PR #7; the transition is now complete — **PR-required is
the rule, not the habit** (ruleset id 23837328). Break-glass semantics are
unchanged: admin bypass only, never routine; record the reason; restore
normal flow immediately after. The action-SHA policy is recorded in the §2
table: required/security/deployment lanes are pinned; agent-review lanes
float by documented design and are the enumerated gap.
**[verified 2026-09-22]** Stage-1 ruleset live: "main history protection"
(ruleset id 23831217, enforcement active) — deletion + non-fast-forward
blocked on `refs/heads/main`, bypass actor RepositoryRole admin
 (bypass mode `always`). Pilot PR #7: https://github.com/randytarampi/dotfiles/pull/7.

**Owner decisions (2026-09-22):** the actionlint download script and
`get.chezmoi.io` installers remain deliberately unpinned so update-system and
fresh installs pull the latest tool versions; this is an accepted supply-chain
trade-off. The d0d5090 break-glass record is retained here: direct push to main
after ruleset activation was a docs-only follow-through of the reflect round,
using the admin bypass once; subsequent work returns to PR-first. Live merge
settings are rebase-only; squash is disabled.

## 4. Local-model analysis

### Decision summary

| Component | Disposition | Rationale and acceptance |
|---|---|---|
| oMLX local vision/observer (discovered model) | **Keep** | Session data records 15 uses. On an M3 Max with 128 GB unified memory, the local path is a credible low-latency/private observer capacity. This is a capacity assessment, not a benchmark claim. Keep while it remains responsive and its outputs are treated as observation rather than final judgement. |
| Cloud audio/vision judgement (discovered model) | **Keep** | Session data records seven uses. Cloud judgement provides a separate capability and avoids treating a local observer as an authority; retain it for cases where quality or multimodal judgement matters more than local privacy/latency. |
| oMLX model pool | **Keep, document** | The live pi inventory records seven oMLX LLM models plus an oMLX-discovered STT model. Keep discovery generic and do not hard-code model names in repo tests or docs. |
| oMLX-discovered STT model | **Historical** | The former OpenCode voice configuration discovered an oMLX STT endpoint. OpenCode v2 removed that V1-only plugin; retain oMLX STT only for other consumers. |
| `whisper-cli` fallback model | **Defer as fallback** | The generic whisper-cli fallback (approximately 1.6 GB) is useful when oMLX is unavailable, but it should not displace the working local primary. Keep installation/discovery bounded and test fallback selection. |
| Provider-specific STT override variable | **Defer** | The phase decision explicitly rejected pinning model names and deferred a repository-owned STT model override until override semantics are requested. |

The trade-off is therefore deliberate: local vision/observer work favours
privacy, availability and machine-side latency; cloud A/V judgement favours a
separate quality ceiling and broader judgement capability. The 128 GB M3 Max
assessment is `[believed]` capacity reasoning from the machine inventory, not a
measured throughput or quality result. The use counts above are session data,
not a quality score. Any claim that one path is more accurate is speculation and
requires a controlled benchmark.

## 5. Residual risks and promotion gates

1. **Windows promotion:** promoted to required after three consecutive green
    runs; the remaining Windows extension is the deliberately Unix-gated doctor
    check. [.github/workflows/ci.yml; item 1]
2. **Nightly promotion:** keep allow-failure until prerequisites, artefacts and
   postconditions make failures meaningful. PI, MOZART and CODEGRAPH package
   installation are not silently promoted into a configuration-only lane.
   [.github/workflows/nightly-integration.yml]
3. **Coverage:** the 27% production floor is a ratchet starting point; the
    current measured value is 36%, not a promise of 100%. [pyproject.toml]
4. **Machine state:** the oMLX upload-limit correction was machine-side; future
   configure scripts must validate it without assuming that this repository owns
   every upstream setting. [docs/VOICE.md]

## 6. Traceability and update rules

This document is self-contained: claims cite repository files and recorded
checks. Update one concern at a time, preserve verified
versus proposed language, and add acceptance evidence when an item closes. Do
not duplicate tier tables or orchestration inventories here; link to
[`docs/TIERS.md`](TIERS.md) and [`docs/ORCHESTRATION.md`](ORCHESTRATION.md).

Key evidence: [`docs/ORCHESTRATION.md`](ORCHESTRATION.md),
[`docs/VOICE.md`](VOICE.md), and the [Phase 4 CI workflow](../.github/workflows/ci.yml).
