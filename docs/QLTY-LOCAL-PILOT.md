# Local Qlty pilot (advisory)

The existing root `qlty.toml` configures a Qlty Cloud plugin; it is **not** the same as the CLI's `.qlty/qlty.toml`. Do not replace it or assume Cloud/CLI rules are identical.

Install the Qlty CLI using the [official quickstart](https://docs.qlty.sh/cli/quickstart), then initialise on a throwaway worktree with `qlty init` to inspect the proposed `.qlty/qlty.toml` and plugin definitions. **Do not run `qlty init` unattended in this repository**: accept only a reviewed, explicit configuration after comparing enabled checks with the existing Qlty Cloud findings.

Commands: `make qlty-check` runs changed-file checks; `make qlty-smells` reports repository-wide maintainability debt. Both fail explicitly if the CLI is absent, but neither is a required gate. The checked-in repository has **not** been initialised for the Qlty CLI; the commands are exploratory and are **not reproducible local gates** until the generated `.qlty/qlty.toml`, tool versions and plugin selection have been reviewed and committed.

Pilot with the known findings from #72 and #74–80: verify matching Ruff/import-order results, EditorConfig precedence, synthetic-secret fixture handling, Bandit test exclusions, and meaningful complexity/duplication advice. Record false positives and rule-version mismatches. Use targeted suppression comments with a reason and issue ID, not repository-wide blind suppressions. Do not fail merges on the existing complexity backlog.

Current Qlty CLI distributions advertise Windows install/binaries, while some quickstart documentation still characterises Windows support as developing. Do not assume unsupported or supported plugin parity from either statement alone: verify the pinned CLI and selected plugins on Windows, macOS and Linux and document precise deviations.

A follow-up **small deterministic changed-file gate** must reproduce `ruff E402` / `F811` (as seen in #74) plus the EditorConfig precedence bug: inject a known invalid import-order or duplicate-definition fixture and verify a nonzero local result; verify correctly indented Python plus valid Markdown list continuation does not produce a misleading indentation failure. Keep full `make verify`, global formatting and whole-repository complexity scans off the fast pre-commit path.

Only after platform parity, reasonable check runtime and a Windows fallback are demonstrated should a follow-up add a **changed-file-only, deterministic** Qlty subset to pre-commit / `make verify` / CI. Keep expensive `qlty smells --all` advisory or on-demand. Local tests remain `make test` / `make verify`; linters do not replace behavioural tests.
