# Clean-host bootstrap: acceptance programme

> **Plan baseline:** `main@b6a73760ac4a9c912db69a0c7de7608aeb0bfc6a`, 2026-11-12. **Status:** source implementation in draft PRs; platform-level fresh-host acceptance remains unverified. Tracker: [#270](https://github.com/randytarampi/dotfiles/issues/270). Programme plan and design: ChatGPT (GPT-6); subsequent implementers/reviewers should retain their own attribution.

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
4. **Native Windows/macOS** [#274](https://github.com/randytarampi/dotfiles/issues/274): reusable Git Bash/PowerShell chezmoi interpreter/path bootstrap, explicit VM receipts and versioned macOS support; WSL2 remains separate. macOS 10.15–14 is legacy core-only/best-effort pending proof.
5. **Embedded/other CPUs** [#275](https://github.com/randytarampi/dotfiles/issues/275): Raspberry Pi 64-bit, Pine A64+ minimal profile, explicit POWER and 32-bit unsupported/experimental handling; physical devices are owner-only.
6. **CI/DX decision** [#276](https://github.com/randytarampi/dotfiles/issues/276): measure workflow duration and re-run churn, then consider fast PR versus full merge/nightly. Do **not** weaken `ci/required` or `deploy` under an untested hypothesis. Coordinate #212 and #267.

## Acceptance evidence and negative cases

For every required platform, record OS release, `uname -m`/native processor, binary versions, exact source SHA, first and second deploy exit codes, `chezmoi verify` and clean diff, expected files, and package/service invocation traces. Test absent Python package manager, unconfigured Codex, existing Codex profile, failed brew install, missing winget, no privilege, no network and unknown architecture. Reject a fake-success state where a skipped component is reported operational.

Container evidence cannot prove package installation on a physical Mac/Windows machine. Owner-only checks include native VM or hardware, secrets, package installation/EULA, service restarts and network exposure. Default CI uses synthetic homes; no real host migration or credentials.

## Risks, dependencies and rollback

Preserve .chezmoi source paths and the stable `ci/required` status check; required CI already tests Windows. Prefer additive platform probes before promotion. Keep new code/test changes atomic per PR; a broken feature can be rolled back by reverting its PR while keeping a functioning core. Never delete user-owned data to recover a failed install. Existing gate-driven service teardown must not be mistaken for no mutation when designing the smoke isolation.

## Plan-versus-delivery ledger

| Phase | Intended | Delivered/source | Acceptance status |
|---|---|---|---|
| Core | optional Codex runtime, tests, docs | PR linked in #271; update after actual CI | NOT RUN on fresh physical host |
| Homebrew | first-run fix, negative tests | PR linked in #272; Linux package policy deferred | NOT RUN on clean Linux package installation |
| Linux | amd64/arm64 deterministic smoke | PR linked in #273 after runner qualification | NOT RUN |
| Windows/macOS | reusable native bootstrap + VM | issue #274; not in initial source tranche | NOT RUN |
| Embedded | read-only capability / real boards | issue #275; separate owner acceptance | NOT RUN |
| CI policy | measured possible tiering | issue #276; **existing required full PR CI preserved** | No branch-rule changes |

Update this ledger with actual PR references, head commits, deviations, reviews and exact results in the same delivery cycle; do not represent a pending CI job or merge as host acceptance.
