# Repository Conventions

This document is the canonical technical reference for script conventions. `AGENTS.md` delegates authority to it for CLI and environment interface policy. Reference material remains in the linked documentation; policy is kept here so the CLI and environment interfaces can be checked automatically.

## Vocabulary

One meaning per term. These words carry different evidence obligations, and using
them loosely is how "it works" ends up meaning four different things.

| Term | Means | Does not mean |
|---|---|---|
| **provider** | A service identity that serves models, e.g. `anthropic`, `ollama-cloud`, `openrouter` | A model, or a client |
| **model ID** | The exact identifier the provider serves, e.g. `glm-5.3-flash` | A marketing version name |
| **alias** / **route** | A name a client asks for, mapped to a provider + model ID by a gateway | That the mapping resolves, or that the provider accepted it |
| **wire** | The protocol shape on the connection: OpenAI `chat_completions`, Anthropic `messages`, OpenAI `responses` | A provider, or a model |
| **tier** / **preset** | A named selection of roles → model IDs, resolved by `tier_registry.py` | A guarantee about which model actually served a request |
| **client** / **consumer** | The installed program that makes the request | That it is configured, or that a task succeeded |
| **capability** | A specific operation a consumer can perform, e.g. tool calling, streaming, vision | Feature parity across consumers |
| **gate** | A `DOTFILES_RUN_*` switch, default `0`, that enables optional work | That the feature is installed, healthy or reachable |
| **installed** | The package or binary exists on this host | Configured, running, or working |
| **CI-verified** | Required checks passed on an exact commit | That any installed client works |
| **host-qualified** | A real operation succeeded on the intended host, with a receipt | That every path on that host works |
| **owner-reported** | The owner said it worked; not independently replayed | Independent evidence |
| **unsupported** | Deliberately not supported at this version, with a re-entry trigger | Broken-by-accident |
| **credential** | A secret value read at runtime | The *name* of the variable that holds it |
| **ready** / **healthy** | A protocol or consumer probe passed after an action | That the service manager accepted a command |

The distinction that matters most in practice: **a generated configuration, a
served model listing and a successful task are three different proofs.** A model
that appears in `/v1/models` may still reject inference; a profile that parses
may never load.

## Markdown layout

Markdown prose is **not hard-wrapped at a fixed column count**. GitHub callout markers such as `> [!NOTE]`, `> [!TIP]` and `> [!IMPORTANT]` must remain on their own source lines, followed by separately quoted content lines; joining a marker with its body breaks alert rendering. Keep an ordinary paragraph or list-item sentence on one physical source line and allow the editor to soft-wrap it for display. Keep newlines that are semantically significant: headings, blank lines between blocks, separate list items, blockquotes, fenced or indented code, tables, reference definitions, front matter, and intentional hard breaks (two trailing spaces or an explicit line-break element). Never fold lines inside a code block or a table merely to satisfy this policy.

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

### Provider base-URL and host overrides

Official SDK environment variables override hardcoded provider URLs across all scripts, taking priority over the `BASE_URLS` defaults in `scripts/lib/constants.py`.

| Env var | Provider | Default | Notes |
|---|---|---|---|
| `ANTHROPIC_BASE_URL` | Anthropic | `https://api.anthropic.com/v1` | Also signals Meridian usage when set |
| `OPENAI_BASE_URL` | OpenAI | `https://api.openai.com/v1` | OpenAI SDK standard |
| `OLLAMA_CLOUD_BASE_URL` | Ollama Cloud | `https://ollama.com/v1` | Repo-owned, not an official SDK variable |

Resolution order in `constants.py` via `get_provider_base_url(provider)`: env override → `BASE_URLS` default → local Ollama fallback.

Local services resolve host and port the same way:

| Service | Host env | Port env | Default |
|---|---|---|---|
| Local Ollama | `OLLAMA_LOCAL_HOST` | `OLLAMA_LOCAL_PORT` | `localhost:11434` |
| Official Ollama | `OLLAMA_HOST` | — | `http://localhost:11434`; scheme+host[:port], overrides the pair above |
| Meridian proxy | `MERIDIAN_HOST` | `MERIDIAN_PORT` | `127.0.0.1:3456` |

Use the helpers rather than re-deriving these: `get_ollama_local_base_url()`, `get_meridian_base_url()` and `is_meridian_configured()`. `is_meridian_configured()` returns true when `MERIDIAN_API_KEY` or `ANTHROPIC_BASE_URL` is set, and is the canonical Meridian detection — do not duplicate it.

### Declarative override keys in JSON configs

`configs/junie/model-groups.json` supports two keys that are resolved and stripped before the config is written:

| Key | Format | Purpose |
|---|---|---|
| `baseUrlEnv` | Env var name | If the named variable is set, overrides `baseUrl` |
| `hostEnvAlt` | Env var name | Alternative host variable in scheme+host[:port] form (e.g. `OLLAMA_HOST`); takes priority over `hostEnv`/`portEnv` |

`scripts/generate-jetbrains-profiles.py` performs the resolution. The keys are stripped from output because Junie does not understand them.

### Canonical Ollama Cloud routing

Ollama Cloud identities use the separate `ollama-cloud/<id>` provider and route directly to `https://ollama.com/v1` with `OLLAMA_API_KEY` in direct mode. When `DOTFILES_USE_LITELLM_PROXY=1`, a client adapter maps that unchanged identity to its exact LiteLLM alias; a missing gateway, key or alias is a configuration error, never a silent direct fallback. `check_ollama_daemon()` is a daemon-health and sign-in probe for consumers that genuinely use installed local `:cloud` models (notably the voice exception) — it does not change provider routing. Cloud stub pulls stay with the install script.

## Markdown file names

All non-platform-owned Markdown filenames must be **lowercase kebab case**: `name.md`, `name-with-parts.md`. An underscore, uppercase letter or mixed-case extension is invalid. This applies to `docs/`, configuration documentation and `.github/ISSUE_TEMPLATE/` templates. Rename a Markdown file and its consumers together, including relative links, absolute repository links pointing at current files, literal paths in scripts, fixture expectations and workflows. Keep immutable historical GitHub commit links pointing to their *historical* filename.

**Reserved filenames:** `AGENTS.md` (agent discovery), `README.md` (repository landing), `SKILL.md` within skill directories (agent skill discovery), `.github/PULL_REQUEST_TEMPLATE.md` (GitHub's PR template), and the existing plugin-owned `dot_config/opencode/oh-my-opencode-slim/orchestrator_append.md` (upstream-managed configuration surface). These are exact exceptions, not a general permission to use snake case.

Review new or renamed Markdown paths and affected consumers in the PR. Run existing `make check-docs-drift`, relevant source/fixture tests and `make verify` as appropriate; these do not independently enforce filename casing or validate every Markdown link. No dedicated filename or link parser is installed. Untracked personal files, downloaded dependencies and generated host assets are outside this repository naming convention. Preserve Git's recorded executable bit and verify affected consumers rather than treating Git's rename detection as proof of application compatibility.

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

The root `qlty.toml` configures Qlty Cloud; it is not a locally generated `.qlty/qlty.toml`. The Makefile exposes **advisory** `make qlty-check` (changed-file analysis) and `make qlty-smells` (repository-wide smells). Neither belongs to required `make verify` or pre-commit while local plugin/version and Windows/macOS/Linux parity remain unqualified. Both commands fail when the Qlty CLI is absent. A fresh checkout has no reviewed local `.qlty/qlty.toml`, so installing the CLI alone does **not** make these commands reproducible or sufficient as gates. To explore them, install the CLI using its official quickstart, create an isolated throwaway worktree and run `qlty init` **there only**. Inspect the proposed configuration, plugins and versions against root `qlty.toml` before adopting any local configuration.

Do not run `qlty init` unattended or introduce repository-wide Prettier/Markdownlint settings solely to make an initial formatter report green: those can activate rules over unrelated historical code and prose. Assess a deterministic changed-file subset in an isolated worktree. Verify that known-invalid and known-valid fixtures discriminate correctly (including Ruff import checks and EditorConfig precedence), track false positives, and use targeted suppressions with reasons. Qlty Cloud checks, local CLI checks, integrated verification and installed-client acceptance are separate evidence types.

## Convention Enforcement

`make verify` runs `check-cli-contract` (manifest-driven CLI surface validation) and `check-env-coverage` (environment-variable documentation, ownership, and alias/deprecation tracking).

When adding a script:

1. Define its capabilities.
2. Add it to `scripts/lib/cli-contract.json`.
3. Add common-argument support.
4. Test `--help` and `--dry-run` as applicable.

See the [orchestration reference](orchestration.md) and [adding components guide](adding.md) for implementation details.

The shell test suite intentionally does not use kcov: version 43.1 hangs while wrapping bats on macOS arm64 (verified 2026-09-22). The bats suite is therefore validated through normal execution rather than kcov.

## User environment section migrations

Issue [#86](https://github.com/randytarampi/dotfiles/issues/86) is separately scoped from the existing deprecated-key rename mechanism. `scripts/migrate-env-sections.py` is **preview-only by default**, opt-in `--apply`, POSIX owner/mode/parent validated, literal assignments only, and always creates a private exclusive `.i2.bak` before atomic replacement. It relocates known active keys into sections specified by the canonical `.env.example` while preserving the effective values of all active variables. Sections are matched **by title at either heading level**: a `# == X ==` sub-section nested inside an owning `# --- X ---` section names its keys' canonical section, and an owner file may style that block with either marker — the finest documented title is canonical, and created blocks reuse the template's own heading style and remain nested under the documented parent heading. Refuse uncertain shells, ambiguous comments, duplicates and symlinks rather than interpreting a private config file. Concurrent-change protection is **best-effort drift detection only**: the guarded preflight and final identity/byte recheck can refuse one writer's update, but there is no cross-process compare-and-swap, so a foreign writer landing inside the final window before replacement can still be overwritten. Only the owner may apply it to their real home, and only in a quiescent maintenance window with no concurrent shell, editor or UI writer. See [orchestration](orchestration.md#opt-in-i2-section-migration-for-user-owned-env-86) for the detailed runbook.

## Source merge versus operational acceptance

A merged pull request, a green CI run and a `COMMENTED` review record **source evidence** only: they do not close their linked GitHub issues or signal that an installed consumer was qualified. Finishing a multi-PR tranche includes, in the same cycle, reconciling the linked issues (delivered vs remaining, with evidence labels) and `docs/modernization-roadmap.md`; issues close only on finite acceptance evidence or a documented owner-accepted unsupported/no-change outcome. An exception that pushes directly to `main` must carry recorded incident rationale, a focused failing/repaired test where feasible, and green post-push CI in the commit or follow-up record; it is the documented last resort, never the routine path. See [orchestration.md](orchestration.md) and [modernization-roadmap.md](modernization-roadmap.md#delivery-reconciliation-matrix-2026-11-08).
