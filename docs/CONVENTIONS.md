# Repository Conventions

This document is the canonical technical reference for script conventions. `AGENTS.md` delegates authority to it for CLI and environment interface policy. Reference material remains in the linked documentation; policy is kept here so the CLI and environment interfaces can be checked automatically.

## Markdown layout

Markdown prose is **not hard-wrapped at a fixed column count**. Keep an ordinary paragraph or list-item sentence on one physical source line and allow the editor to soft-wrap it for display. Keep newlines that are semantically significant: headings, blank lines between blocks, separate list items, blockquotes, fenced or indented code, tables, reference definitions, front matter, and intentional hard breaks (two trailing spaces or an explicit line-break element). Never fold lines inside a code block or a table merely to satisfy this policy.

Both the repository `.editorconfig` and the deployed `dot_editorconfig` disable Markdown maximum line length. When using Prettier manually, pass `--prose-wrap never`; when using markdownlint manually, disable MD013 (fixed-width line length). Do not introduce repository-wide Prettier or markdownlint configuration as part of this migration: both automatically activate Qlty formatter checks for unrelated Markdown/style conventions, creating widespread failures. The `make lint` whitespace check and `make fix` text normalizer intentionally exclude Markdown: trimming two trailing spaces would silently remove a hard break. These are editor and authoring conventions, not new mandatory formatter dependencies.

The portable review prompt and skill copies are generated from `configs/skills/randy-review/` rather than hand-edited. After changing their canonical sources, run `python3 scripts/generate-review-bundles.py --write`, `make check-review-bundles`, and `make check-ci-assets`. Do not run standalone formatters over their output paths; preserve generation parity.

## CLI Capability Contract

Scripts declare capabilities in [`scripts/lib/cli-contract.json`](../scripts/lib/cli-contract.json):

| Capability | Contract |
| --- | --- |
| `public` | Must support `--help`, print usage, and exit 0. |
| `mutates` | Must support `--dry-run` to preview without writing. |
| `backups` | Must support `--no-backup`; backups are enabled by default. |
| `tier_selector` | Must require canonical `--preset <TIER>`; positional tier operands are rejected. |
| `scope_selector` | Must support `--mode {global,project}`. |
| `orchestrates` | Calls child scripts. Must declare `child_scripts` edges. |

Flags irrelevant to a script **must be rejected** with exit code 2 (usage error), rather than silently accepted. Exit codes are:

* `0` — success
* `1` — runtime failure
* `2` — usage error

## Argument Forwarding

`--dry-run` and `--no-backup` propagate automatically from parent scripts to child scripts that accept them. `--help` on a parent script does NOT propagate to children. Parent `--help` terminates at the parent: the parent prints its own help and exits 0; it is not forwarded to children. Script-specific flags, such as `--local-fallback-*`, are explicit and are passed only when relevant.

Shell scripts forward arguments with arrays. Under `set -euo pipefail`, the `-u` (nounset) option treats empty arrays as unset, so the safe `${array[@]+"${array[@]}"}` pattern is required — bare `"${array[@]}"` fails with `unbound variable` when the array is empty:

```bash
COMMON_ARGS=("--dry-run" "--no-backup")
"$CHILD_SCRIPT" ${COMMON_ARGS[@]+"${COMMON_ARGS[@]}"}
```

Python scripts use helper functions that construct argument lists from the parsed namespace. `configure-all.sh` parses common arguments once, forwards them to every child that accepts them, aggregates child failures, and exits 1 if any child fails. When `--dry-run` is active, scripts must not write to the filesystem or create backups. `--dry-run` takes precedence over `--no-backup`.

## Environment Variable Taxonomy

Environment variables have three ownership tiers:

| Owner | Examples |
| --- | --- |
| `DOTFILES_*` (repo-owned) | `DOTFILES_RUN_OPENCODE_SETUP`, `DOTFILES_PROJECT_PRESET` |
| Upstream-native | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `AWS_ACCESS_KEY_ID`, `OLLAMA_HOST`, `OMLX_HOST` |
| `DOTFILES_<TOOL>_*` (adapter layer) | Tool-specific configuration wrapping an upstream concept |

There is one canonical name per concept. Deprecated aliases are migrated one-way by `migrate-env-gates.py`. Precedence is:

Canonical names and compatibility boundaries must be explicit:

- `GH_TOKEN` is canonical; `GITHUB_TOKEN` is derived only at integration boundaries that require it, such as `act` and GitHub Copilot CLI.
- `OLLAMA_HOST` is canonical and upstream-native; `OLLAMA_LOCAL_HOST` plus `OLLAMA_LOCAL_PORT` are composition fallbacks for scripts that need host and port separately.
- `OPENCODE_SERVER_PORT` (the fixed OpenCode Web service port, normally 4096) and `OPENCODE_PORT` (the random multiplexer/editor-pane port) are distinct concepts, not aliases.

`CLI flags` > `project .env.local` > `project .env` > `~/.env` > `process env` > `defaults`

`env.py` loads the project environment files and overwrites `os.environ`, so process environment values are lower precedence than those files.

Orchestrating scripts declare `failure_policy`: `aggregate` (continue on child failure, exit 1 at end if any failed) or `fail_fast` (abort on first child failure).

Config shadowing is prohibited: two variables must not represent the same concept without a documented relationship.

## Naming Conventions

Script naming and lifecycle policy is defined in the [Scripting Conventions section of `AGENTS.md`](../AGENTS.md), including `configure-*.py/sh`, `install-*.sh`, `verify-*.py`, `check-*.py`, `detect-*.py`, `generate-*.py`, and `get-*.py`. That section also specifies boilerplate, `run_once` versus `run_onchange`, gates, and hash triggers.

## Cross-Language Interface Parity

[`scripts/lib/cli-contract.json`](../scripts/lib/cli-contract.json) is the single source of truth for common flags. Both `scripts/lib/common_args.sh` (Bash) and `scripts/lib/cli_helpers.py` (Python) must conform to it. Contract tests in `tests/cli/test-common-args.sh` verify both implementations against shared fixtures.

Python parsers use `allow_abbrev=False` to match Bash: abbreviated long options are not accepted. Help comparison checks the exit code and required option names, not byte-for-byte formatting.

## Dependency Version Policy

Pinned dependencies use an evidence-based refresh policy — never blind range relaxation. Empirical sandbox probes (2026-10-05) established:

### OpenCode plugins (`configs/opencode/opencode-plugins.json`)

All managed plugins track the **floating `@latest` alias** (user preference, 2026-10-08; deliberately reverses the exact-pins policy chosen on 2026-10-05). Probes against opencode 1.18.34 established the semantics:

- `name@latest` installs into a stable cache directory (`name@latest/`) and resolves the newest release at install time; the concrete resolved version is readable only inside `node_modules/<pkg>/package.json`.
- Warm-cache re-runs do not float on their own — the resolved version stays until the cache is refreshed. A manifest change (hash-triggered `run_onchange_07`) drives the reinstall.
- `check-plugin-consistency.py` accepts `@latest` specs: `is_pinned_spec` permits the alias, directory detection matches the `@latest` cache entry, and the concrete-version `package.json` fallback is skipped for alias specs (it could never match a literal "latest").

Operator accepts that `@latest` means "newest, possibly breaking" — zero-major packages in particular may receive breaking changes without notice. Refresh semantics: reinstall on manifest-hash change re-resolves the newest release; a deliberate refresh without a manifest edit means clearing the corresponding plugin cache directory (or the whole cache) and re-running the install script, then `make check-plugin-consistency` plus the focused tests.

### Pi packages (`scripts/configure-pi.py`)

Unversioned `npm:<name>` specs are **retained**. Probes (pi 1.0.3):

- `_ensure_packages` is idempotent by package NAME only (version-blind string comparison), so re-running configure-pi never refreshes anything.
- Refresh is manual: `pi update --extension <source>` for one package or `pi update --extensions` for all, run deliberately by a human, then verify the tool still functions (`check-plugin-consistency` does not cover pi).

### GitHub Actions and pip

Dependabot covers both (`github-actions` weekly floating-major, `pip` weekly); GitHub Actions major bumps are additionally guarded by `scripts/lib/tests/test_action_major_refs.py`. These ecosystems already match the bounded-refresh policy.

Rationale: the ecosystems differ deliberately. OpenCode plugin specs are user-preference floating (`@latest`, accepted breaking risk); Pi packages are unversioned names reconciled only by a deliberate human `pi update`. Neither is "verified non-breaking" — they are "latest, possibly breaking", an accepted trade for current releases without manual bump churn. Where update semantics must stay fully controlled (GitHub Actions majors, guard-testable dependencies), pins or guarded floats remain the rule.

## Qlty quality checks and formatter scope

The root `qlty.toml` configures Qlty Cloud; it is not a locally generated `.qlty/qlty.toml`. The Makefile exposes **advisory** `make qlty-check` (changed-file analysis) and `make qlty-smells` (repository-wide smells). Neither belongs to required `make verify` or pre-commit while local plugin/version and Windows/macOS/Linux parity remain unqualified. Both commands fail when the Qlty CLI is absent.

Do not run `qlty init` unattended or introduce repository-wide Prettier/Markdownlint settings solely to make an initial formatter report green: those can activate rules over unrelated historical code and prose. Assess a deterministic changed-file subset in an isolated worktree. Verify that known-invalid and known-valid fixtures discriminate correctly (including Ruff import checks and EditorConfig precedence), track false positives, and use targeted suppressions with reasons. Qlty Cloud checks, local CLI checks, integrated verification and installed-client acceptance are separate evidence types.

## Convention Enforcement

`make verify` runs `check-cli-contract` (manifest-driven CLI surface validation) and `check-env-coverage` (environment-variable documentation, ownership, and alias/deprecation tracking).

When adding a script:

1. Define its capabilities.
2. Add it to `scripts/lib/cli-contract.json`.
3. Add common-argument support.
4. Test `--help` and `--dry-run` as applicable.

See the [orchestration reference](ORCHESTRATION.md) and [adding components guide](ADDING.md) for implementation details.

The shell test suite intentionally does not use kcov: version 43.1 hangs while wrapping bats on macOS arm64 (verified 2026-09-22). The bats suite is therefore validated through normal execution rather than kcov.
