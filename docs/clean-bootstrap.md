# Clean-host bootstrap: acceptance programme

> **Plan baseline:** `main@b6a73760ac4a9c912db69a0c7de7608aeb0bfc6a`, 2026-11-12. **Status:** source PRs #277–#279, #282 and Windows interpreter #292 merged; the read-only host inventory is a separate source slice (#293). Fresh-host, physical-board and installed-client acceptance remain unverified. Tracker: [#270](https://github.com/randytarampi/dotfiles/issues/270). Programme plan and design: ChatGPT (GPT-6); subsequent implementers/reviewers should retain their own attribution.

## Target contract and affected consumers

This work extends the existing three-layer [orchestration architecture](orchestration.md), not a parallel installer. User-facing entrypoint remains [install.md](install.md); `Makefile`, chezmoi templates/scripts, `configure-all.sh`, Brewfile/wingetfile categories, and the existing CI are the consumers.

| Level | Minimum outcome | Disallowed implied promise |
|---|---|---|
| L0 core | Render/apply shell, Git/SSH/editor dotfiles in empty HOME, twice, without drift or optional runtime | AI tools, package managers, credentials, GUI or services installed |
| L1 packages | Selected OS+arch package category installed with actual receipts | Every Brewfile formula works on Linux or ARM |
| L2 agents | Named installed CLI configured, real consumer task when applicable | Generator-created JSON proves inference or authenticated routing |
| L3 services | Owner-enabled service health, actual protocol and consumer acceptance | Container smoke proves launchd/systemd/native hardware or paid-free entitlement |

A disabled optional gate is **skipped**, an enabled missing dependency **fails**, and unsupported hardware is **reported as unsupported**. Never use silent paid/network fallback, mutate user secrets, or deploy new public listeners.

## Prioritized phases and bounded PR scopes

1. **Core dependency** [#271](https://github.com/randytarampi/dotfiles/issues/271): preserve existing Codex selection semantics while making a never-configured optional client independent of Poetry. Test absent/present CLI, existing config, explicit gate, explicit skip and unchanged user state.
2. **First-install Homebrew** [#272](https://github.com/randytarampi/dotfiles/issues/272): recompute binary path after installer, guard unavailable/failed binary and test with stubbed installation. Linux package manifest and third-party tap eligibility remain a separate phase, not a presumed corollary.
3. **Linux native/containers** [#273](https://github.com/randytarampi/dotfiles/issues/273): start with disposable native-runner empty-HOME proof. Add non-root Docker smoke on ubuntu:24.04 and ubuntu:26.04 amd64/arm64; state native versus emulated evidence. Promote only demonstrated stable combinations into the existing `ci/required` dependency.
4. **Native Windows/macOS** [#274](https://github.com/randytarampi/dotfiles/issues/274): required two-pass Git Bash deployment on Windows Server x64 and Windows 11 ARM64 plus macOS 15 Intel and macOS 26 Apple Silicon, with exact CPU/OS assertions and preserved floating latest regression lane. Required CI proves GitHub VMs, not retail Windows 11 x64, WinGet provisioning, WSL2, PowerShell-only, real Mac GUI clients or macOS 10.15–14 legacy core-only compatibility.
5. **Embedded/other CPUs** [#275](https://github.com/randytarampi/dotfiles/issues/275): Raspberry Pi 64-bit, Pine A64+ minimal profile, explicit POWER and 32-bit unsupported/experimental handling; physical devices are owner-only.
6. **CI/DX decision** [#276](https://github.com/randytarampi/dotfiles/issues/276): measure workflow duration and re-run churn, then consider fast PR versus full merge/nightly. Do **not** weaken `ci/required` or `deploy` under an untested hypothesis. Coordinate #212 and #267.

## Read-only host capability inventory (#275)

Use `python3 scripts/report-bootstrap-capabilities.py --json` to collect a non-mutating, deliberately conservative OS/CPU/memory, binary-presence, package-manager and display-environment report. The machine-readable schema explicitly says `operational_verified: false`; L1 packages, L2 clients and L3 services are always `not_qualified` by this probe. An ARM64 CPU does not identify a particular board, a binary on PATH does not prove an agent functions, and missing DISPLAY does not prove a headless host. The required native Ubuntu smoke lanes now execute the probe for real runner OS/architecture evidence before deploying. See [Host capability report](bootstrap-host-capabilities.md) and [#275](https://github.com/randytarampi/dotfiles/issues/275). Owner-only physical Pi/Pine64 receipts and unsupported architecture decisions remain future work.

## Acceptance evidence and negative cases

For every required platform, record OS release, `uname -m`/native processor, binary versions, exact source SHA, first and second deploy exit codes, `chezmoi verify` and clean diff, expected files, and package/service invocation traces. Test absent Python package manager, unconfigured Codex, existing Codex profile, failed brew install, missing winget, no privilege, no network and unknown architecture. Reject a fake-success state where a skipped component is reported operational.

Container evidence cannot prove package installation on a physical Mac/Windows machine. Owner-only checks include native VM or hardware, secrets, package installation/EULA, service restarts and network exposure. Default CI uses synthetic homes; no real host migration or credentials.

## Risks, dependencies and rollback

Preserve .chezmoi source paths and the stable `ci/required` status check; required CI already tests Windows. Prefer additive platform probes before promotion. Keep new code/test changes atomic per PR; a broken feature can be rolled back by reverting its PR while keeping a functioning core. Never delete user-owned data to recover a failed install. Existing gate-driven service teardown must not be mistaken for no mutation when designing the smoke isolation.

## Plan-versus-delivery ledger

| Phase | Intended | Delivered/source | Acceptance status |
|---|---|---|---|
| Core | optional Codex runtime, tests, docs | [PR #277](https://github.com/randytarampi/dotfiles/pull/277) **merged** at `5c4bd2e7`; required PR and post-merge CI passed | NOT RUN on a fresh physical host |
| Homebrew | first-run fix, negative tests | [PR #278](https://github.com/randytarampi/dotfiles/pull/278) **merged** at `5c3f7295`; Linux package eligibility remains #272 | NOT RUN on a clean Linux package installation |
| Linux | amd64/arm64 deterministic L0 smoke | [PR #279](https://github.com/randytarampi/dotfiles/pull/279) **merged** at `2a8f3277`; four Ubuntu 24.04/26.04 x64/arm64 native jobs passed, CodeRabbit's two findings fixed | Source/CI **VERIFIED**; physical hosts and packages NOT RUN |
| Networkless Linux runtime | required Ubuntu 24.04 x64 Docker acceptance; four OS/CPU rows proposed | [PR #282](https://github.com/randytarampi/dotfiles/pull/282) **merged**, with one Ubuntu 24.04 x64 non-root `--network none` runtime and a real IPv4 denial probe. The next bounded CI tranche adds 24.04/26.04 x64/ARM64 native-container pairs and IPv6 denial; **not verified until exact-head CI passes** | Host VM and container evidence do not prove Raspberry Pi/Pine64 boards or live systemd services |
| Windows/macOS | reusable native bootstrap + VM | issue #274; not in initial source tranche | NOT RUN |
| Embedded | read-only capability / real boards | issue #275; separate owner acceptance | NOT RUN |
| CI policy | measured possible tiering | issue #276; **existing required full PR CI preserved** | No branch-rule changes |

Update this ledger with actual PR references, head commits, deviations, reviews and exact results in the same delivery cycle; do not represent a pending CI job or merge as host acceptance.

## Deviation discovered by initial native smoke (2026-11-12)

The first #279 matrix reached both deployment passes but **failed** its no-service-control assertion on all four runners: disabled LiteLLM and Open WebUI chezmoi paths invoked `systemctl --user disable --now` and `daemon-reload` despite no managed unit files. [#280](https://github.com/randytarampi/dotfiles/issues/280) records the concrete failure. The third PR now includes a narrow, tested guard for absent Linux units and managed process paths. This dependent correction is deliberately inside the smoke PR rather than weakening its guard or duplicating an unreviewed service refactor. Existing installed-user service teardown must still be tested on the owner host; native ARM runner success alone is not that proof.

## Runner-label linter provenance (2026-11-12)

The third PR's four Ubuntu 24.04/26.04 x64/ARM64 native smoke jobs **all passed** on [CI run 43318825914](https://github.com/randytarampi/dotfiles/actions/runs/43318825914), but aggregate verification failed because pinned actionlint `1.7.12` (released before Ubuntu 26.04 runner GA) reports the official `ubuntu-26.04` and `ubuntu-26.04-arm` hosted labels as unknown. Rather than inventing a non-native ARM job or suppressing all runner validation, `.github/actionlint.yaml` has one exact path-scoped pattern covering **only** these two labels. The real CI job/OS/architecture identity is asserted independently in the smoke workflow. Remove this compatibility exception when the pinned actionlint release knows those hosted labels. This is a tooling-version accommodation, not a relaxation of `ci/required` or Linux smoke acceptance.

## Static review remediation (2026-11-12)

Qlty's first review of #279 raised Docker `DL3066` (non-numeric `USER`), `DL4006` (installer pipe without `pipefail`) and Bash `SC2016` (single-quoted embedded script). The Dockerfile now explicitly creates and selects UID/GID 10001 and sets Bash pipefail for subsequent RUN; the smoke now writes the inner verification script to its throwaway HOME so the parent HOME cannot expand accidentally and shellcheck can inspect it. There is no new privileged Docker flag, user-owned HOME mount, live service or network route. These corrections are not a claim that the optional Docker image has been built locally.

## Second-pass self-review: unit-file provenance (2026-11-13)

On re-entry, the first #280 guard proved only that a regular unit file existed, which was insufficient for user-owned state preservation: a foreign unit with the same basename could have been disabled/deleted. The guard now checks **both** the description and exact virtualenv executable path found in legacy repo-generated units, leaving an unrecognised regular file and symlink untouched with a warning. Tests exercise legacy-owned, foreign regular, symlinked and completely absent units. **Negative-case trade-off:** a user-modified managed unit with no recognisable signature is deliberately preserved and needs manual removal; this is safer than deleting an ambiguous user service. These tests do not replace real host service acceptance.

## Final adversarial safety refinement (2026-11-13)

The #279 template-backed service tests previously forwarded the developer's ambient environment, including any API/provider values, into a synthetic test subprocess. They now pass only a tiny explicit environment (HOME/USER/PATH/TRACE, disabled gates and NO_COLOR). The Linux core smoke's denied-command trace also covers `pkill`, closing an omission in the no-service-control check. This still does not enforce network isolation against arbitrary Python/Node HTTP clients; it rejects known package/network/service-management executables while the isolated `env -i` prevents inheriting operator credentials. The README links this plan and the `make bootstrap-smoke` target. These refinements require fresh exact-head CI after publication; historical green checks are not sufficient.

## CodeRabbit adversarial correction (2026-11-13)

CodeRabbit's current-head review of [PR #279](https://github.com/randytarampi/dotfiles/pull/279) exposed two real failure modes. A grep for a service description and executable pathname could accept **commented-out** or otherwise inactive systemd directives and delete a user-modified unit; and a nonzero `chezmoi diff` inside `[[ -n "$(cmd)" ]]` could silently yield a clean-drift result. The remediation uses one shared read-only systemd unit matcher, checking exactly one active `[Unit] Description` and one active `[Service] ExecStart` against the known generated command envelope (including numeric port), and only tears down matched units. The fixture now rejects commented directives, suffixed executable paths, wrong sections, duplicate directives, and unrelated commands even when the known binary also exists. The isolated smoke now fails on nonzero diff exit, and includes a non-root assertion and synthetic secret-canary check.

The **no-egress claim remains unproven** by native command-stubbed smoke: no host firewall or process network namespace is installed. [#281](https://github.com/randytarampi/dotfiles/issues/281) owns a separate networkless Docker runtime acceptance (with an actual non-stubbed socket negative control). Until its evidence is verified, no publication should call the native smoke a general no-network sandbox. Optional Docker reproduction should be launched with `--network none`; build-time downloads remain outside that *runtime* guarantee. CI and owner-host checks must be distinguished in the issue ledger.

## Network-contained Docker tranche: issue #281 (2026-11-13)

[PR #282](https://github.com/randytarampi/dotfiles/pull/282) delivered a representative Ubuntu 24.04 x64 runtime. The next source tranche expands the **same required** `bootstrap-networkless` job to a four-row Ubuntu 24.04/26.04 × x64/ARM64 matrix, paired with the existing four native L0 smoke rows. Every Docker **runtime**, unlike the prerequisite download/build phase, uses `--network none`, a non-root UID 10001, dropped capabilities and no host HOME/Docker socket mounts. Both numeric IPv4 and IPv6 socket probes must report network-denied errors; `/proc/net/route` and available `/proc/net/ipv6_route` must have no **usable/forwarding** default route. Linux's built-in IPv6 reject-only `::/0` loopback fallback (flag `RTF_REJECT`) is allowed because it cannot forward traffic; the real AF_INET6 socket denial remains mandatory. The build and runtime OS/CPU are asserted against the matrix expectations. Within the same runtime the real `make deploy` runs twice and chezmoi must report no drift. Docker image tags are not digest-pinned in this tranche and build-time downloads are not covered by the no-egress claim.

**Scope decision revised (2026-11-14):** the owner explicitly prioritised matrix-backed, continuously asserted acceptance over manual host verification. On draft PR #299's earlier head `dfc48bd6`, [CI 43534320066](https://github.com/randytarampi/dotfiles/actions/runs/43534320066) **PASS** proved all four native-platform Docker builds and 24.04/26.04 x64/ARM64 networkless runtimes; CodeQL also passed. The final remediation head requires its own independent exact-head checks before promotion. A 25-minute per-row timeout bounds accidental hangs. Four Docker builds become required after merging the PR; record the completed run's wall time and billed-minutes estimate before claiming a CI/DX improvement. If a row fails due to infrastructure rather than source, stop and record evidence instead of marking the job advisory or fabricating support. #276 owns the measured cost/feedback tradeoff. This remains L0 CI evidence only: not native-machine egress containment, Linux Homebrew package qualification, actual Raspberry Pi/Pine64 boot or full service lifecycle.

## Source merge reconciliation (2026-11-13)

GitHub's completed **rebase merges** are #277 → `main@5c4bd2e7`, #278 → `main@5c3f7295`, and #279 → `main@2a8f3277`. All three PRs had exact-head `ci/required` and CodeQL PASS before merging; #277 and #278 also had green post-merge main CI, and the post-merge [CI run 43397074830](https://github.com/randytarampi/dotfiles/actions/runs/43397074830) **PASS**. This is **source acceptance**, not real host installation. Linked #272/#273 were unexpectedly marked closed after merge and were reopened because Linux package-eligibility and network/physical host acceptance still exist. #281 is delivered only as draft source and a successfully exercised *networkless Docker job*, not a merged operational guarantee. #274/#275/#276 remain explicit later phases. Keep issues open for the actual owner-only evidence or a documented change in scope.
