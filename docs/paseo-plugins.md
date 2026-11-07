# Paseo community plugins (owner-only installation)

These community plugins are **unsandboxed daemon code**, not OpenCode plugins or ordinary installed CLI dependencies. Install only after inspecting their pinned source and permissions. The [Paseo catalogue](https://paseo.sh/plugins) can change independently of a floating npm `@latest` Paseo installation.

## Desired six plugins

| Plugin | Gate | Operational considerations |
|---|---|---|
| [stevecastaneda/progress](https://paseo.sh/plugins/stevecastaneda/progress) | Installed Paseo; Node >=22.18 for its CLI | Agent progress is not automatic: the plugin's command and skill require separate, owner-approved installation. |
| [paseo-cafe/cafe](https://paseo.sh/plugins/paseo-cafe/cafe) | Installed Paseo | Downloads registry metadata, supports automatic updates; review its auto-update and registry settings. |
| [uiyzzi/codexbar-usage](https://paseo.sh/plugins/uiyzzi/codexbar-usage) | **`codexbar` executable on daemon `PATH`** | Homebrew's `cask "codexbar"` in `Brewfile.desktop.dev` is installation intent, not proof of the CLI. The plugin also supports the .app CLI, but this manager intentionally applies the owner's stricter PATH requirement. |
| [roundreddot/better-paseo](https://paseo.sh/plugins/roundreddot/better-paseo) | Paseo >=0.12 | Can inspect stored conversations, agent tasks and files; optional model-driven title generation can use network providers. |
| [gpambrozio/skills](https://paseo.sh/plugins/gpambrozio/skills) | Installed Paseo | Reads skill files from the daemon host and invokes skills by sending messages; it does not replace the repo's skills registry. |
| [dutchakdev/machine-status](https://paseo.sh/plugins/dutchakdev/machine-status) | **Published compatibility: Paseo 0.8–0.13, macOS** | At the owner's expected floating Paseo 0.15.0 this is **excluded** until upstream confirms support. It exposes cache deletion, process termination and Docker controls; confirm approval boundaries before enabling. |

## Operator workflow

1. Run `make paseo-plugins-check` to list desired installations and explicit exclusions. This reads `paseo --version` and (when installed) `node --version` only; it never reads Paseo config or installs packages.
2. Verify the actual Paseo version, Node version, the CodexBar daemon executable path, and the published plugin source/revision. On Paseo 0.15, the machine-status plugin is deliberately skipped; do not bypass this with a forced install.
3. Quiesce users of Paseo settings/config because [#173](https://github.com/randytarampi/dotfiles/issues/173) is unresolved; back up private state outside the checkout.
4. Owner explicitly invokes `DOTFILES_RUN_PASEO_PLUGINS_SETUP=1 make paseo-plugins-apply`. To include CodexBar, separately verify its executable is reachable by the running Paseo daemon (not just the shell), then use `DOTFILES_RUN_PASEO_PLUGINS_SETUP=1 python3 scripts/install-paseo-plugins.py --apply --codexbar-daemon-verified`; this flag is an owner attestation, **not** automatic daemon PATH discovery. Each command is `paseo plugin add owner/slug`, with fixed arguments and no shell. Any failure aborts with no unsafe rollback.
5. Inspect `paseo plugin list`, daemon/plugin logs, plugin permissions, mobile app rendering and reload behaviour. Repeat installation only deliberately; the CLI may refresh versions and is not asserted to be idempotent here.

## Negative and security cases

Missing Paseo, no daemon-verified PATH CodexBar, absent/old Node, unsupported versions or platforms, missing owner gate, invalid version probe and failed plugin installs are explicit outcomes. The plan never enables automatic installs in `make deploy`; no credentials or plaintext config are written by this script. A plugin may independently store/update its own state or make network requests; plugin installation is not a zero-side-effect operation and should not be treated as safe simply because CI passes.

Rollback is plugin-specific via the native Paseo plugin UI/CLI after inspecting whether it owns generated state. Do not delete `~/.paseo` or an agent's progress files. See [Paseo remote](paseo-remote.md) for the private listener / public access policy.

## Verification handoff

- `PYTHONPATH=scripts/lib poetry run python -m pytest scripts/lib/tests/test_install_paseo_plugins.py -q`
- `make check-cli-contract`, `make check-env-coverage`, `make check-hashes`, `make verify`.
- Owner-only test of actual installed plugin UI, action approvals, progress skill, CodexBar output, and wrong-platform/missing-prerequisite cases.
- No installed plugin, real daemon, desktop UI, or active session has been tested by the GitHub-only implementation author.
