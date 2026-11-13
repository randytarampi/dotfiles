# Read-only host bootstrap capability report

> **Status:** source-level inventory, not a platform support certification. Implements the first non-mutating assessment slice of [#275](https://github.com/randytarampi/dotfiles/issues/275) in the [#270](https://github.com/randytarampi/dotfiles/issues/270) bootstrap programme. Design/implementation: ChatGPT (GPT-6), 2026-11-13.

Run from the committed checkout with Python 3 (no package install, root, provider token or service-manager call required):

```bash
python3 scripts/report-bootstrap-capabilities.py
python3 scripts/report-bootstrap-capabilities.py --json
```

The report reads the operating-system family and native CPU architecture, `/etc/os-release` and `/proc/meminfo` where available, and checks whether core utilities, package-manager executables and selected agent CLIs exist on `PATH`. It checks only whether the Linux display environment is *advertised* and whether a service-manager executable exists. It does **not** contact the network, inspect credentials, read service configuration, run installed agents, infer device identity from ARM64, write a configuration file or enable anything.

## Conservative meaning of each field

| Field | Possible result | Interpretation |
|---|---|---|
| `host.architecture` and `assessment.architecture_class` | Actual `platform.machine()`; normalized amd64/arm64/arm32/power/unknown | A CPU string is not a Raspberry Pi or Pine64 board receipt |
| `assessment.core_l0` | `candidate_unverified`, `experimental_unverified`, `not_qualified` | A source-level prerequisite assessment only, **never a passing deploy** |
| `host.core_tools_present` | bash, make, chezmoi | Binary presence, not sufficient version, interpreter compatibility or authorization |
| `host.package_tools_present` | apt-get, brew, dnf, pacman, winget | Detection only; no manifest, user entitlement or installation claim |
| `host.agent_clis_present` | Name-only CLI presence | Does not imply authentication, local model compatibility or inference |
| `host.service_manager_binary_present` | true / false | Does not imply user session, a running service or permission |
| `host.display_environment` | advertised, not_advertised, unknown | Environment-variable evidence only; not proof of a GPU/desktop/headless machine |
| `host.memory_gib` | Linux MemTotal (GiB) or null | Null means **unknown**, not zero; memory alone cannot qualify local inference |
| `assessment.packages_l1`, `agents_l2`, `services_l3` | always not_qualified | They require real package, installed-client and service receipts |
| `assessment.operational_verified` | always false | Prevents a check of binary presence from being promoted into operational acceptance |

64-bit ARM on a Raspberry Pi or Pine A64+ is an **L0 candidate** when prerequisites exist, but device-specific pins, boot firmware, memory and network characteristics are not inferred. ARM32 and POWER are **experimental**, not automatically supported. An unknown CPU/OS or missing core tool is **not qualified**. For headless Linux without `DISPLAY` or `WAYLAND_DISPLAY`, the report notes that display variables were not advertised; it does not claim an actual headless device. A machine below 4 GiB is flagged as a caution for heavyweight local model eligibility; no AI model is automatically started or selected.

## Acceptance and negative cases

The existing required native Ubuntu 24.04/26.04 amd64/arm64 L0 smoke lanes run the new report on their **actual runners**, before the pinned chezmoi installation and disposable-home deployment. This verifies the reporting code executes under those native OS/CPU combinations; it does not add any package installation, contact a provider, or replace physical hardware evidence.

Fixture tests cover unknown CPUs/OSes, armv7l and ppc64le, absent core tools, absent displays, low/unknown RAM, malformed Linux memory records, shell-looking strings in `os-release`, and provider-token canaries that must never appear in the report. This is read-only evidence and should remain separate from owner-only physical Pi/Pine64 acceptance.

For real hardware acceptance, record board/model, OS release, native CPU, RAM, source SHA, installed version of each prerequisite, first and second deploy status, `chezmoi verify`, clean diff, and actual package/service traces. [#275](https://github.com/randytarampi/dotfiles/issues/275) stays open until those receipts or an explicit unsupported/experimental disposition are documented. Do not rely on this report to assert L1, L2 or L3 support.
