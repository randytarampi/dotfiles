# Repository Conventions

This document is the canonical technical reference for script conventions.
`AGENTS.md` delegates authority to it for CLI and environment interface policy.
Reference material remains in the linked
documentation; policy is kept here so the CLI and environment interfaces can be
checked automatically.

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

Flags irrelevant to a script **must be rejected** with exit code 2 (usage
error), rather than silently accepted. Exit codes are:

* `0` — success
* `1` — runtime failure
* `2` — usage error

## Argument Forwarding

`--dry-run` and `--no-backup` propagate automatically from parent scripts to
child scripts that accept them. `--help` on a parent script does NOT propagate
to children. Parent `--help` terminates at the parent: the parent prints its
own help and exits 0; it is not forwarded to children.
Script-specific flags, such as `--local-fallback-*`, are explicit and are
passed only when relevant.

Shell scripts forward arguments with arrays. Under `set -euo pipefail`,
the `-u` (nounset) option treats empty arrays as unset, so the safe
`${array[@]+"${array[@]}"}` pattern is required — bare `"${array[@]}"`
fails with `unbound variable` when the array is empty:

```bash
COMMON_ARGS=("--dry-run" "--no-backup")
"$CHILD_SCRIPT" ${COMMON_ARGS[@]+"${COMMON_ARGS[@]}"}
```

Python scripts use helper functions that construct argument lists from the
parsed namespace. `configure-all.sh` parses common arguments once, forwards
them to every child that accepts them, aggregates child failures, and exits 1
if any child fails. When `--dry-run` is active, scripts must not write to the
filesystem or create backups. `--dry-run` takes precedence over
`--no-backup`.

## Environment Variable Taxonomy

Environment variables have three ownership tiers:

| Owner | Examples |
| --- | --- |
| `DOTFILES_*` (repo-owned) | `DOTFILES_RUN_OPENCODE_SETUP`, `DOTFILES_PROJECT_PRESET` |
| Upstream-native | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `AWS_ACCESS_KEY_ID`, `OLLAMA_HOST` |
| `DOTFILES_<TOOL>_*` (adapter layer) | Tool-specific configuration wrapping an upstream concept |

There is one canonical name per concept. Deprecated aliases are migrated
one-way by `migrate-env-gates.py`. Precedence is:

Canonical names and compatibility boundaries must be explicit:

- `GH_TOKEN` is canonical; `GITHUB_TOKEN` is derived only at integration
  boundaries that require it, such as `act` and GitHub Copilot CLI.
- `OLLAMA_HOST` is canonical and upstream-native; `OLLAMA_LOCAL_HOST` plus
  `OLLAMA_LOCAL_PORT` are composition fallbacks for scripts that need host and
  port separately.
- `OPENCODE_SERVER_PORT` (the fixed OpenCode Web service port, normally 4096)
  and `OPENCODE_PORT` (the random multiplexer/editor-pane port) are distinct
  concepts, not aliases.

`CLI flags` > `project .env.local` > `project .env` > `~/.env` > `process env` > `defaults`

`env.py` loads the project environment files and overwrites `os.environ`, so
process environment values are lower precedence than those files.

Orchestrating scripts declare `failure_policy`: `aggregate` (continue on child
failure, exit 1 at end if any failed) or `fail_fast` (abort on first child
failure).

Config shadowing is prohibited: two variables must not represent the same
concept without a documented relationship.

## Naming Conventions

Script naming and lifecycle policy is defined in the [Scripting Conventions
section of `AGENTS.md`](../AGENTS.md), including `configure-*.py/sh`,
`install-*.sh`, `verify-*.py`, `check-*.py`, `detect-*.py`, `generate-*.py`,
and `get-*.py`. That section also specifies boilerplate, `run_once` versus
`run_onchange`, gates, and hash triggers.

## Cross-Language Interface Parity

[`scripts/lib/cli-contract.json`](../scripts/lib/cli-contract.json) is the
single source of truth for common flags. Both
`scripts/lib/common_args.sh` (Bash) and `scripts/lib/cli_helpers.py` (Python)
must conform to it. Contract tests in
`tests/cli/test-common-args.sh` verify both implementations against shared
fixtures.

Python parsers use `allow_abbrev=False` to match Bash: abbreviated long
options are not accepted. Help comparison checks the exit code and required
option names, not byte-for-byte formatting.

## Convention Enforcement

`make verify` runs `check-cli-contract` (manifest-driven CLI surface
validation) and `check-env-coverage` (environment-variable documentation,
ownership, and alias/deprecation tracking).

When adding a script:

1. Define its capabilities.
2. Add it to `scripts/lib/cli-contract.json`.
3. Add common-argument support.
4. Test `--help` and `--dry-run` as applicable.

See the [orchestration reference](ORCHESTRATION.md) and [adding components
guide](ADDING.md) for implementation details.
