# Linux Homebrew package eligibility: fail-before-install static audit

> **Status:** source-level safety gate for [#272](https://github.com/randytarampi/dotfiles/issues/272), not operational package support certification. Implemented by ChatGPT (GPT-6), 2026-11-16. The first-install Homebrew fix is already merged in [#278](https://github.com/randytarampi/dotfiles/pull/278).

The default committed `dev: true` category selects `Brewfile.dev`, which contains macOS cask entries. The Linux package gate previously acquired Homebrew and began `brew bundle` before discovering that these directives are not part of the Linux package lane. The read-only static audit now runs **before** Homebrew discovery/download/install when `DOTFILES_RUN_PACKAGES_SETUP=1` on Linux. It inspects the actually selected Brewfiles, rejects casks and malformed directives, then stops without running `brew` or changing user state. Mac deployment and default-off core L0 are unchanged.

From the checkout, inventory the default two manifests **without mutation**:

```bash
python3 scripts/check-linux-brewfile-eligibility.py --json --manifest Brewfile --manifest Brewfile.dev
python3 scripts/check-linux-brewfile-eligibility.py --enforce --manifest Brewfile --manifest Brewfile.dev
```

The second command currently fails before any package installation because `Brewfile.dev` has casks. Use `python3 ... --manifest Brewfile` to inspect only the base category, **not** as proof that the base taps and formulae work on Ubuntu. Per-machine chezmoi category overrides determine which files an actual deployment audits; `dev` defaults to true and `dev_cli` is always selected. Check `.chezmoidata/categories.yaml` and the resolved `[data.categories]` in the owner's chezmoi state. No category is silently rewritten by this change.

## Classification and safety contract

| Report status | Meaning |
|---|---|
| `blocked_linux_cask` | Linux-incompatible cask; `--enforce` refuses before Homebrew mutation |
| `blocked_syntax` | Unrecognized/malformed source declaration; refusal |
| `macos_named_needs_evidence` | Known macOS-oriented name, but **not** a verified Linux incompatibility without direct evidence |
| `needs_linux_formula_or_tap_evidence` | Name declared, current Linux/architecture entitlement and formula support **unknown** |
| `external_runtime_not_qualified` | npm/uv directive; separate package-manager execution and eligibility unverified |

A zero-blocker result has `packages_l1: not_qualified` and `operational_verified: false`. The audit deliberately never queries the network, trusts third-party taps, executes formula hooks, runs `brew bundle` or inspects credentials. It reports package names and line numbers, not source-line contents. It is an **early failure guard**, not a comprehensive Linux allowlist: unknown taps/formulae can still fail or mutate state if a user proceeds. Do not promote the full Linux Brewfile to supported based on this audit alone.

## Next evidence for #272

1. Record Ubuntu 24.04/26.04 × x64/ARM64 `brew --version`, `brew bundle check`/formula availability for the selected candidate category, and exact tap/CPU failures in a disposable host. Build a constrained Linux subset or explicit exclusion **from observed results**; do not silently drop package intentions from the default Mac manifests.
2. Run an owner-approved, opt-in, disposable real `brew bundle` and second pass with no user-owned state and no hidden EULA/privilege changes; capture exact return codes, installed versions and rollback.
3. Only then mark L1 packages as qualified for that OS/CPU/manifest combination and decide whether issue #272 can close. Real package installation is neither part of L0 CI nor this initial static-gate PR.

**Rollback:** revert this focused PR to remove early static refusal. That restores the old, unqualified Linux package behaviour; it is not a substitute for a proper Linux manifest. No host operation or active category selection is changed by reading this document.
