# Dotfiles modernization roadmap

> **Status:** Phase 5 lane B roadmap, dated 2026-09-20
> **Scope:** dotfiles productionalization, portability, local-model operations and documentation
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

- `[verified]` Phase 1 through Phase 4 are complete at `128125b`. The recorded
  validation is `make verify`, `make test` (140 tests), `make ci-verify`,
  `poetry check` and actionlint; the production coverage measurement was 33%
  against a 25% floor (evidence: this repository's Phase 1-4 commits and
  [pyproject.toml](../pyproject.toml)).
- `[verified]` The Windows deploy is a portability probe and remains
  `continue-on-error`; macOS and Ubuntu are the required baseline. The nightly
  lane currently runs only OPENCODE, MCP and AGENT_GUIDANCE with postconditions.
  [.github/workflows/ci.yml; .github/workflows/nightly-integration.yml]
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
platform-specific doctor checks; it is not permission for a broad rewrite
(see the shell-to-Python inventory below for script-level evidence).

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

### 3. Add oMLX audio-upload validation and normalization

**Priority:** High · **Effort:** M · **Dependencies:** the existing
`configure-omlx` scripts and the oMLX service restart path.

The machine-side `~/.omlx/settings.json` value
`server.max_audio_upload_size` was the unitless string `128`, meaning 128 bytes;
it caused STT HTTP 413 responses. It was corrected machine-side to `128MB`,
after which the transcription endpoint returned HTTP 200. No repository script
currently manages this value.

**Acceptance criteria:** a configure-time check accepts documented byte and
unit forms, normalizes or reports an unsafe value without silently changing
unrelated settings, verifies the effective limit before STT use, and has tests
for the unitless, valid and missing cases. The implementation must not pin a
particular STT model name.

### 4. Ratchet production coverage modestly

**Priority:** Medium · **Effort:** M per quarter · **Dependencies:** the
measured production baseline and subprocess coverage already in place.

The current production floor is 25%; the measured result is 33% after the
Phase 4 scenario harness. [pyproject.toml; Makefile test target] Raise the floor in small
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
| 1 | `scripts/configure-all.sh` (`readlink -f`) | Preserve dependency ordering, warn-on-fail semantics and CLI contract; test path resolution on Windows and Unix. [scripts/configure-all.sh] |
| 2 | `scripts/run-local-review.sh` (`readlink -f`, `mapfile`) | Preserve review stages and exit statuses; add a fixture for the Bash-version-sensitive input path. [scripts/run-local-review.sh] |
| 3 | `scripts/update-nvm-globals.sh` | Replace Homebrew and Unix path assumptions only where the probe demonstrates need; test package-manager selection. [scripts/update-nvm-globals.sh] |
| 4 | `scripts/install-acp-adapters.sh` | Preserve adapter versions and package-manager intent; test Windows installation and a no-network dry run. [scripts/install-acp-adapters.sh] |
| 5 | `scripts/setup-bin-symlinks.sh` | Preserve fresh-deploy behaviour, link targets and idempotency; test Windows-compatible link or fallback behaviour. This is always executed by `run_onchange_05`. [.chezmoiscripts/run_onchange_05-setup-bin-symlinks.sh.tmpl] |

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
| High | Docker | Package and deploy paths expect Docker-compatible local workflows and defaults. | Already packaged, not configured (`Brewfile.desktop.dev` cask `docker-desktop`, `wingetfile.dev` `Docker.DockerCLI`): add a gated `config.json` baseline; document daemon-dependent checks. |
| High | AWS CLI | AWS profiles are an expected operator boundary for cloud scripts; credentials remain user-owned. | Already packaged, not configured (`Brewfile.dev.ops` `awscli`, `wingetfile.dev.ops` `Amazon.AWSCLI`): add a non-secret `~/.aws/config` baseline; gate profile creation. |
| High | kubectl | Kubernetes operations need an explicit CLI and opt-in kubeconfig handling. | Package missing: add package coverage and a gated, non-secret kubeconfig/default-context policy; never generate credentials. |
| High | Pulumi | Infrastructure work needs backend and organisation defaults, while state and secrets are sensitive. | Already packaged, not configured (`Brewfile` tap, `Brewfile.dev.ops`, `wingetfile.dev.ops`): add a gated backend/org configuration; document login and state ownership. |
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

## 4. Local-model analysis

### Decision summary

| Component | Disposition | Rationale and acceptance |
|---|---|---|
| oMLX local vision/observer (discovered model) | **Keep** | Session data records 15 uses. On an M3 Max with 128 GB unified memory, the local path is a credible low-latency/private observer capacity. This is a capacity assessment, not a benchmark claim. Keep while it remains responsive and its outputs are treated as observation rather than final judgement. |
| Cloud audio/vision judgement (discovered model) | **Keep** | Session data records seven uses. Cloud judgement provides a separate capability and avoids treating a local observer as an authority; retain it for cases where quality or multimodal judgement matters more than local privacy/latency. |
| oMLX model pool | **Keep, document** | The live pi inventory records seven oMLX LLM models plus an oMLX-discovered STT model. Keep discovery generic and do not hard-code model names in repo tests or docs. |
| oMLX-discovered STT model | **Keep as primary** | The live voice configuration already discovers an oMLX STT endpoint, and the corrected upload limit produced HTTP 200. Add validation for the upload limit rather than replacing the primary model. |
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

1. **Windows promotion:** the probe must first produce actionable failures and
   bounded fixes; only then should `continue-on-error` be removed.
   [.github/workflows/ci.yml]
2. **Nightly promotion:** keep allow-failure until prerequisites, artefacts and
   postconditions make failures meaningful. PI, MOZART and CODEGRAPH package
   installation are not silently promoted into a configuration-only lane.
   [.github/workflows/nightly-integration.yml]
3. **Coverage:** the 25% production floor is a ratchet starting point, not a
   promise of 100%. [pyproject.toml]
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
