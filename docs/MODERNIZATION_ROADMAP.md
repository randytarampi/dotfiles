# Dotfiles modernization roadmap

> **Status:** Phase 5 lane B roadmap, dated 2026-09-20
> **Scope:** dotfiles productionalization, portability, local-model operations and documentation
> **Confidence:** `[verified]` is supported by the deepwork ledger, repository files or a recorded check; `[believed]` is a bounded interpretation; `[aspirational]` is a target.

## 1. Document contract

This is the durable plan for deferred work from the productionalization
session. The authoritative research ledger is
`.slim/deepwork/dotfiles-productionalization.md`; this document links to that
ledger and to repository evidence rather than copying volatile session state.
The roadmap does not authorize changes to tier configuration, package policy,
secrets, or CI gates without a separate approved work item.

Effort estimates are relative implementation estimates: **S** is hours,
**M** is one to two focused days, **L** is several days, and **XL** is a
multi-session change. They are estimates, not recorded durations.

## 2. Baseline and completed work

- `[verified]` Phase 1 through Phase 4 are complete at `128125b`. The recorded
  validation is `make verify`, `make test` (140 tests), `make ci-verify`,
  `poetry check` and actionlint; the production coverage measurement was 33%
  against a 25% floor. [deepwork:487-550]
- `[verified]` The Windows deploy is a portability probe and remains
  `continue-on-error`; macOS and Ubuntu are the required baseline. The nightly
  lane currently runs only OPENCODE, MCP and AGENT_GUIDANCE with postconditions.
  [deepwork:463-476,535-550]
- `[completed-in-this-pass]` The orchestration range now names the actual
  `run_onchange_04` through `run_onchange_29` scripts, and its inventory includes
  `install-acp-adapters`. The `wingetfile.dev` cross-references now use the
  corresponding Brewfile identifiers. [docs/ORCHESTRATION.md:62-66,165-198;
  wingetfile.dev:1-38]

## 3. Ordered work items

### 1. Close the cross-platform deploy probe

**Priority:** High · **Effort:** L · **Dependencies:** Phase 4 Windows failures,
the audit inventory below, and a reproducible Windows runner.

Fix only failures demonstrated by the Windows deploy lane. The audit identifies
`readlink -f`, `mapfile`, Homebrew paths, LaunchAgents, `launchctl`, and
platform-specific doctor checks; it is not permission for a broad rewrite.
[deepwork:424-448]

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
behaviour. [deepwork:496-528]

**Acceptance criteria:** each enabled gate has an installed prerequisite,
deterministic artefacts and a failure assertion; service, security and system
default gates remain excluded from shared runners; the report distinguishes
skipped, failed and passed gates; then `continue-on-error` is removed and the
workflow is made required after an observed stabilization period.

### 3. Add oMLX audio-upload validation and normalization

**Priority:** High · **Effort:** M · **Dependencies:** the existing
`configure-omlx` scripts and the oMLX service restart path.

The machine-side `~/.omlx/settings.json` value
`server.max_audio_upload_size` was the unitless string `128`, meaning 128 bytes;
it caused STT HTTP 413 responses. It was corrected machine-side to `128MB`,
after which the transcription endpoint returned HTTP 200. No repository script
currently manages this value. [deepwork:315-321]

**Acceptance criteria:** a configure-time check accepts documented byte and
unit forms, normalizes or reports an unsafe value without silently changing
unrelated settings, verifies the effective limit before STT use, and has tests
for the unitless, valid and missing cases. The implementation must not pin a
particular STT model name.

### 4. Ratchet production coverage modestly

**Priority:** Medium · **Effort:** M per quarter · **Dependencies:** the
measured production baseline and subprocess coverage already in place.

The current production floor is 25%; the measured result is 33% after the
Phase 4 scenario harness. [deepwork:389-418,487-491] Raise the floor in small
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
Phase 4 inventory. Mechanical ports come first because these scripts are
always-executed or high fan-out, not because Python is preferred everywhere.

| Order | Candidate | Evidence and bounded acceptance |
|---|---|---|
| 1 | `scripts/configure-all.sh` (`readlink -f`) | Preserve dependency ordering, warn-on-fail semantics and CLI contract; test path resolution on Windows and Unix. [deepwork:428] |
| 2 | `scripts/run-local-review.sh` (`readlink -f`, `mapfile`) | Preserve review stages and exit statuses; add a fixture for the Bash-version-sensitive input path. [deepwork:429] |
| 3 | `scripts/update-nvm-globals.sh` | Replace Homebrew and Unix path assumptions only where the probe demonstrates need; test package-manager selection. [deepwork:432] |
| 4 | `scripts/install-acp-adapters.sh` | Preserve adapter versions and package-manager intent; test Windows installation and a no-network dry run. [deepwork:434] |
| 5 | `scripts/setup-bin-symlinks.sh` | Preserve fresh-deploy behaviour, link targets and idempotency; test Windows-compatible link or fallback behaviour. This is always executed by `run_onchange_05`. [deepwork:445,526-528] |

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
no credentials are added by this roadmap.

| Priority | Tool | Why it matters | Recommended action |
|---|---|---|---|
| High | Docker | Package and deploy paths expect Docker-compatible local workflows and defaults. | Add Docker CLI/Desktop package coverage and a gated `config.json` baseline; document daemon-dependent checks. |
| High | AWS CLI | AWS profiles are an expected operator boundary for cloud scripts; credentials remain user-owned. | Add AWS CLI package coverage and a non-secret `~/.aws/config` baseline; gate profile creation. |
| High | kubectl | Kubernetes operations need an explicit CLI and opt-in kubeconfig handling. | Add package coverage and a gated, non-secret kubeconfig/default-context policy; never generate credentials. |
| High | Pulumi | Infrastructure work needs backend and organisation defaults, while state and secrets are sensitive. | Add package coverage and a gated backend/org configuration; document login and state ownership. |
| Medium | gcloud | Cloud tooling is useful but no active repo workload currently requires a generated profile. | Add package coverage only if a gate-backed workload is identified; otherwise document intentionally absent. |
| Medium | mongosh / Compass | MongoDB is partially covered through skills and MCP; the CLI/app boundary is not configured. | Add package coverage and optional shell defaults only for an approved MongoDB workflow; otherwise document the partial coverage. |
| Medium | psql | Database troubleshooting benefits from a client and `.psqlrc`, but no current gate requires it. | Add package coverage and a small gated `.psqlrc` when a supported workflow exists; otherwise document absent. |
| Medium | Snyk | Security tooling is a possible policy gate, but no current verification target invokes it. | Document intentionally absent until a repository gate and authentication-free policy path exist. |
| Medium | Stripe CLI | Stripe development uses a CLI where local webhook workflows are enabled. | Add package coverage and document login/webhook setup; add a gate only with a testable local workflow. |
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
AWS/MongoDB/skills coverage. [deepwork:80-89]

### 7. Refresh council policy without changing it in this lane

**Priority:** Medium · **Effort:** M · **Dependencies:** the separately owned
tier/configuration lane and a model-policy decision record.

The council assessment remains recommendation-only here. The recorded evidence
favours replacing `nemotron-3-ultra` with `glm-5.3-flash` for speed, cost and
coding, or `deepseek-v4.1-flash` for diversity, while retaining `glm-5.3` and
`kimi-k3`; benchmark release dates are not authoritative. [deepwork:207-221]

**Acceptance criteria:** the policy owner records the selected seat, provider
set and fallback behaviour; the tier docs, registry tests and configuration are
updated together; and no preset change is made as a side effect of this
roadmap document.

## 4. Local-model analysis

### Decision summary

| Component | Disposition | Rationale and acceptance |
|---|---|---|
| oMLX local vision/observer using `gemma-4-12B` | **Keep** | Session data records 15 uses. On an M3 Max with 128 GB unified memory, the local path is a credible low-latency/private observer capacity. This is a capacity assessment, not a benchmark claim. Keep while it remains responsive and its outputs are treated as observation rather than final judgement. |
| Cloud audio/vision judgement using `gpt-5.6-luna` | **Keep** | Session data records seven uses. Cloud judgement provides a separate capability and avoids treating a local observer as an authority; retain it for cases where quality or multimodal judgement matters more than local privacy/latency. |
| oMLX model pool | **Keep, document** | The live pi inventory records seven oMLX LLM models, alongside `parakeet-tdt-0.6b-v3` STT and `whisper-large-v3-turbo`. [deepwork:44-48] Keep discovery generic and do not hard-code a model name in repo tests or docs. |
| Parakeet STT | **Keep as primary** | The live voice configuration already discovers an oMLX STT endpoint, and the corrected upload limit produced HTTP 200. [deepwork:35-39,315-321] Add validation for the upload limit rather than replacing the primary model. |
| `whisper-cli` `ggml-large-v3-turbo` | **Defer as fallback** | The fallback is approximately 1.6 GB and is useful when oMLX is unavailable, but it should not displace the working local primary. Keep installation/discovery bounded and test fallback selection. |
| Provider-specific STT override variable | **Defer** | The phase decision explicitly rejected pinning model names and deferred a repository-owned STT model override until override semantics are requested. [deepwork:116-121,130-138] |

The trade-off is therefore deliberate: local vision/observer work favours
privacy, availability and machine-side latency; cloud A/V judgement favours a
separate quality ceiling and broader judgement capability. The 128 GB M3 Max
assessment is `[believed]` capacity reasoning from the machine inventory, not a
measured throughput or quality result. The use counts above are session data,
not a quality score. Any claim that one path is more accurate is speculation and
requires a controlled benchmark.

## 5. Residual risks and promotion gates

1. **Windows promotion:** the probe must first produce actionable failures and
   bounded fixes; only then should `continue-on-error` be removed. [deepwork:463-469]
2. **Nightly promotion:** keep allow-failure until prerequisites, artefacts and
   postconditions make failures meaningful. PI, MOZART and CODEGRAPH package
   installation are not silently promoted into a configuration-only lane.
   [deepwork:470-476,519-528]
3. **Coverage:** the 25% production floor is a ratchet starting point, not a
   promise of 100%. [deepwork:389-418]
4. **Machine state:** the oMLX upload-limit correction was machine-side; future
   configure scripts must validate it without assuming that this repository owns
   every upstream setting. [deepwork:315-321]

## 6. Traceability and update rules

The Phase 5 ledger, the Phase 4 Windows table and repository evidence remain the
source of truth for facts. Update one concern at a time, preserve verified
versus proposed language, and add acceptance evidence when an item closes. Do
not duplicate tier tables or orchestration inventories here; link to
[`docs/TIERS.md`](TIERS.md) and [`docs/ORCHESTRATION.md`](ORCHESTRATION.md).

Key evidence: [deepwork state](../.slim/deepwork/dotfiles-productionalization.md),
[`docs/ORCHESTRATION.md`](ORCHESTRATION.md),
[`docs/VOICE.md`](VOICE.md), and the [Phase 4 CI workflow](../.github/workflows/ci.yml).
