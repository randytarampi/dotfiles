# Local Qlty pilot (advisory)

The existing root `qlty.toml` configures a Qlty Cloud plugin; it is **not** the same as the CLI's `.qlty/qlty.toml`. Do not replace it or assume Cloud/CLI rules are identical.

Install the Qlty CLI using the [official quickstart](https://docs.qlty.sh/cli/quickstart), then initialise on a throwaway worktree with `qlty init` to inspect the proposed `.qlty/qlty.toml` and plugin definitions. **Do not run `qlty init` unattended in this repository**: accept only a reviewed, explicit configuration after comparing enabled checks with the existing Qlty Cloud findings.

Commands: `make qlty-check` runs changed-file checks; `make qlty-smells` reports repository-wide maintainability debt. Both fail explicitly if the CLI is absent, but neither is a required gate.

Pilot with the known findings from #72 and #74–80: verify matching Ruff/import-order results, EditorConfig precedence, synthetic-secret fixture handling, Bandit test exclusions, and meaningful complexity/duplication advice. Record false positives and rule-version mismatches. Use targeted suppression comments with a reason and issue ID, not repository-wide blind suppressions. Do not fail merges on the existing complexity backlog.

Only after Linux and macOS parity, reasonable check runtime and a Windows fallback are demonstrated should a follow-up add a **changed-file-only, deterministic** Qlty subset to pre-commit / `make verify` / CI. Keep expensive `qlty smells --all` advisory or on-demand. Local tests remain `make test` / `make verify`; linters do not replace behavioural tests.
