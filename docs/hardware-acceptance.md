# Hardware acceptance and the self-hosted runner decision

**Status: NOT REGISTERED. No runner exists, and none should be added until the
GO criteria below are satisfied and the owner records the decision.**

This document exists because the obvious way to get recurring evidence from the
owner's Raspberry Pi and Pine64 boards — register them as GitHub Actions
self-hosted runners — is also a way to hand arbitrary code execution to anything
that can get a workflow onto a public repository.

## Approved change in support scope — 22 November 2026

Physical Raspberry Pi/Pine/Armbian validation is **optional**, tracked separately by [#384](https://github.com/randytarampi/dotfiles/issues/384). [#275](https://github.com/randytarampi/dotfiles/issues/275) now owns bounded ARM64/headless and unsupported/experimental architecture classification, not live board qualification. GitHub-hosted Ubuntu ARM64 is a useful native Ubuntu VM surrogate but cannot verify Armbian kernel, device boot, storage or peripherals. [#385](https://github.com/randytarampi/dotfiles/issues/385) tracks an optional delegated SSH validation path; neither optional issue blocks mainstream [#270](https://github.com/randytarampi/dotfiles/issues/270). No self-hosted runner has been registered or authorized by this decision.

This document's public-repository runner threat model and GO/NO-GO criteria remain authoritative. An isolated private pull-only verifier is preferred to a public-repository runner; temporary JIT execution requires a disposable/reimaged execution image and network isolation, not merely an ephemeral registration.

## The problem being solved

`#384` tracks optional native hardware evidence; `#275` now tracks conservative architecture classification. No automated physical-board job or acceptance receipt has been recorded. Until a safely isolated executor exists, board qualification still requires an authorized operator's observation; do not infer it from hosted Ubuntu ARM64 CI.

## Why a repository-scoped runner is not automatically safe

The repository is public. GitHub's own guidance recommends against self-hosted
runners for public repositories, and the reasons are structural rather than
about which events a job listens on:

| Vector | Why an `if:` guard does not close it |
|---|---|
| Workflow edits | A merged PR can change any workflow. The guard is in the code being changed. |
| `workflow_run` | A workflow triggered by another workflow can inherit a different trust level than expected. |
| Reusable workflows | A called workflow can come from elsewhere than the caller. |
| Compromised action | A pinned action's tag can be re-pointed unless pinned to a SHA; a compromised action executes as the runner. |
| Artifacts and caches | Untrusted content written by one job can be read by a later one on a persistent runner. |
| Persistent state | Unlike hosted runners, the machine survives between jobs, so any foothold persists. |
| Fork PRs | The classic entry point. Mitigations exist, but they are filters, not isolation. |
| Lateral movement | Even a personal board on a home network is a foothold on that network. |

A repository-scoped label is not a sandbox. `permissions: contents: read` limits
the *token*, not the *process*.

## Options, with their real costs

### A. Hosted runners only (no hardware automation)

GitHub-hosted runners stay the only execution surface. Hardware evidence remains
owner-produced by hand, guided by
[bootstrap-acceptance-receipts.md](bootstrap-acceptance-receipts.md).

- Cost: optional `#384` remains unqualified until actual board evidence; recurring operator attention if that work is later funded.
- Risk: lowest. Nothing new executes on the owner's hardware.

### B. Ephemeral self-hosted runner *(recommended if the hardware can be isolated)*

Register the board as a **repository-scoped, `--ephemeral`** runner on a
dedicated unprivileged account, with:

- every job restricted to `schedule` and `workflow_dispatch` — never
  `pull_request`, `pull_request_target`, or an unvetted `workflow_run`;
- all third-party actions pinned to full commit SHAs;
- no repository or personal secrets reachable from the job;
- a dedicated machine or a rebuildable image, not the daily driver;
- an explicit reboot/reimage procedure.

`--ephemeral` unregisters the runner after one job; it does **not** erase the
operating system or remove a foothold left on the machine. Re-registering the
same board without rebuilding its execution environment preserves malicious
files, services and processes. Treat the host as compromised after untrusted
code runs, and use a disposable VM/image with a verified per-job reset.

- Cost: a runner controller, per-job image reset, network isolation and recurring maintenance; not just a workflow file.
- Risk: moderate. The board is a real host; isolation is configured, not
  structural.

### C. Private control plane or pull-based verifier

The public repository keeps hosted runners. A **private** acceptance repository
(or a service on the board that polls) holds the hardware test definition, fetches
only a reviewed immutable SHA, runs it under a locked-down account, and publishes
a redacted receipt.

- Cost: a second repository, a second identity, a credential-upload story, and
  another thing to maintain — which is the failure mode this whole programme
  exists to reduce.
- Risk: lowest per-execution, highest per-maintenance.

**Recommendation:** for a public repository, prefer **C** (private control
plane/pull verifier) when it can be kept simple; otherwise choose **A** until
there is an explicitly approved isolation design. **B** is a conditional
alternative only if the runner uses a disposable, rebuildable execution image
that is reset between jobs, and the host is isolated from the home network and
personal state. A dedicated account plus `--ephemeral` registration alone is
**not sufficient**. If neither can be maintained safely, optional `#384` remains unqualified or is closed `not planned` with an explicit owner-accepted limitation, rather than inventing acceptance.

## Resolving the event contradiction

An earlier draft of this programme said "self-hosted jobs are restricted to
`schedule` and `workflow_dispatch`" and then specified a canary "triggered on
`push` to `main`". Those cannot both be true.

**Rule: one explicit event contract per trust domain.**

- **Hardware runners (untrusted input surface):** `schedule` and
  `workflow_dispatch` only.
- **Post-merge canary (trusted input):** may run on `push` to `main`, but only
  after the trusted control plane has confirmed `ci/required` passed on the
  immutable SHA. A public `workflow_run` event must not itself confer privilege.

A post-merge canary is **not** a pre-merge required check. It can mark a
promotion unsafe; it cannot block a merge.

## GO checklist

All must hold before registering anything. Any failure is a NO-GO, and the
fallback is option A or C.

- [ ] Dedicated, expendable hardware — not the daily driver, not holding private
      user files or production credentials.
- [ ] Dedicated unprivileged OS account; no personal SSH keys, cloud tokens or
      password-manager access reachable from it.
- [ ] Every hardware job triggers only on `schedule` or `workflow_dispatch`.
- [ ] Every third-party action pinned to a full commit SHA.
- [ ] Runner registered `--ephemeral` **and** execution environment destroyed/reimaged between jobs (or equivalent independently checked isolation).
- [ ] Network egress limited to what the test needs.
- [ ] Reimage/reboot procedure written down, and exercised at least once.
- [ ] Receipt publication uses the provenance rules in
      [bootstrap-acceptance-receipts.md](bootstrap-acceptance-receipts.md).
- [ ] Owner has read this document and recorded the residual risk they accept.

## What is not authorised by this document

Registering a runner, opening an inbound port, storing a broad `gh` token on a
board, or attaching the primary workstation. This is a decision framework, not a
licence.
