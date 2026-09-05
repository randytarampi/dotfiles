# AI Agent Guidance

> **This is the authoritative agent guidance document for this dotfiles repo.**
> For human-facing quick start, commands, and package management, see `README.md`.
> For deep reference material, see the `docs/` directory.

## Documentation Conventions

`AGENTS.md` is authoritative **policy** only. Reference content lives in `docs/` and `README.md` — link, don't duplicate. When editing, check `docs/` first and link there.

The repo is a chezmoi source directory. For the full directory tree and script inventory, see [docs/ORCHESTRATION.md](docs/ORCHESTRATION.md). For package management details, see `README.md`.

---

## Installation

See [docs/INSTALL.md](docs/INSTALL.md) for the complete installation, upgrade, and verification flow.
In normal use, `make deploy` is the canonical entrypoint.
Run it twice on first setup to surface idempotency gaps.

---

## Scripting Conventions

### Boilerplate

Every script must follow this pattern:

```bash
#!/usr/bin/env bash
set -euo pipefail

# Resolve real script location (works when invoked via symlink from ~/bin/)
_SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
LIB_DIR="$SCRIPT_DIR/lib"

source "$LIB_DIR/common.sh"
source "$LIB_DIR/common_args.sh"  # For --help, --dry-run, --no-backup support
# source "$LIB_DIR/env.sh"        # If env loading needed
# source "$LIB_DIR/tier_detect.sh" # For tier auto-detection (detect_tier)
# source "$LIB_DIR/tier_args.sh"   # For local fallback arg forwarding (build_tier_extra_args)
# Use scripts/lib/discover_models.py for Ollama discovery in Python scripts
# Use scripts/lib/ai_models.py for model prefix mappings, temperatures, and strip_provider_prefix
# Use scripts/lib/constants.py for shared constants (BASE_URLS, get_meridian_base_url, get_ollama_local_base_url, get_provider_base_url, is_meridian_configured, PROVIDER_BASE_URL_ENVS)
# Use scripts/lib/file_utils.py for backup_file() and write_text_file()
# Use scripts/lib/opencode_config.py for get_available_tiers() and build_tier_args()
# Use scripts/lib/cli_helpers.py for add_common_args(), forward_common_args(), add_local_fallback_args()
```

For Python scripts, use `cli_helpers.add_common_args(parser)` for `--dry-run` and `--no-backup`,
and `cli_helpers.add_local_fallback_args(parser)` for `--local-fallback-*` flags. Use
`allow_abbrev=False` in argparse to match Bash parser parity.

### Naming

- `configure-*.py/sh` — tool/environment configuration scripts
- `install-*.sh` — installation scripts (idempotent)
- `verify-*.py` — verification/check scripts
- `detect-*.py` — detection/inspection scripts
- `generate-*.py` — code/model generation scripts
- `get-*.py` — query/inspection scripts

### Structure

All scripts follow: parse args → load env → gate check → main logic → ok/die

### run_once vs run_onchange policy

- `run_once_*` is reserved for truly one-time operations only: directory creation, security hardening, permissions, and similar setup that should not repeat on every apply.
- `run_onchange_*` is the default for everything else. If a script can be re-run safely, it should usually live here with hash triggers.
- Prefer idempotent scripts so `make deploy` can invoke orchestration twice without side effects.

### Hash trigger convention

- Use `# <path>: {{ include "<path>" | sha256sum }}` comments in `.chezmoiscripts/run_onchange_*` templates.
- Add hash triggers whenever a script depends on configuration inputs, generated manifests, or config fragments.
- Run `make check-hashes` before committing to confirm every input is covered.

### Gate naming convention

- Use `DOTFILES_RUN_*_SETUP` for opt-in orchestration gates.
- Gates default to `0` and should cover one logical feature at a time.
- Document new gates in `.env.example` and in the orchestration docs.
- When splitting a gate into sub-gates, add migration entries to `scripts/migrate-env-gates.py` using the inheritance pattern so existing users' settings flow to the new sub-gates.

### CLI Capability Contract

All scripts must conform to the [CLI Capability Contract](docs/CONVENTIONS.md). Key requirements:

- **Public scripts** must accept `--help` (exit 0, side-effect-free).
- **Mutator scripts** must accept `--dry-run` (skip writes, log what would be done).
- **Backup-capable scripts** must accept `--no-backup` (backup-on default; `--backup` is NOT used).
- **Tier selectors** require the canonical `--preset` flag; positional operands are rejected.
- **Irrelevant flags must be rejected** with exit code 2 (usage error), not silently accepted.
- **Exit codes**: 0 success, 1 runtime failure, 2 usage error.

Every script is registered in `scripts/lib/cli-contract.json` with its capabilities, accepted flags, and child scripts. Run `make check-cli-contract` to verify conformance.

### Arg Forwarding

Common flags (`--dry-run`, `--no-backup`) propagate parent→child via shared forwarding arrays:

- **Shell**: `COMMON_FORWARD_ARGS` array from `common_args.sh`; pass `${COMMON_FORWARD_ARGS[@]+"${COMMON_FORWARD_ARGS[@]}"}` to children that accept common flags. The `${array[@]+"..."}` pattern is required because `set -u` treats empty arrays as unset — bare `"${array[@]}"` fails with `unbound variable` when the array is empty.
- **Python**: `forward_common_args(args)` from `cli_helpers.py`; pass the returned list to child subprocess calls.
- **Capability-based**: Only forward flags the child actually accepts. A child without `--dry-run` must not receive it.

Local-fallback flags (`--local-fallback-preset/role/placeholder`) forward explicitly to children that support them.

### Env-Var Taxonomy

Environment variables follow an ownership-based taxonomy (see [docs/CONVENTIONS.md](docs/CONVENTIONS.md)):

- `DOTFILES_*` — repo-owned (gates, tier settings, project vars)
- Upstream-native — keep as-is (e.g. `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `AWS_*`)
- `DOTFILES_<TOOL>_*` — adapter layer for tools that need repo-managed config

One canonical name per concept. Deprecated aliases migrated one-way via `migrate-env-gates.py`. Run `make migrate` after pulling updated dotfiles to migrate `~/.env`.

### Convention Enforcement

`make verify` enforces conventions automatically:

- `make check-cli-contract` — validates CLI surfaces against the manifest (smoke-tests `--help`, checks required flags by capability, verifies invalid flags are rejected)
- `make check-env-coverage` — validates env vars are documented in `.env.example`, tracks deprecated aliases and known alias pairs
- `make check-hashes` — validates hash trigger coverage in `run_onchange` scripts
- `make check-ci-assets` — validates hashes of CI/local-only review assets; when editing any file listed in `configs/review/assets-manifest.json`, run `make update-ci-assets` in the same step as the edit, before running verify

When adding a new script:
1. Define its capabilities in `scripts/lib/cli-contract.json`
2. Add common args via `common_args.sh` (shell) or `cli_helpers.py` (Python)
3. Test `--help`, `--dry-run`, and invalid flag rejection
4. Document any new env vars in `dot_dotfiles/shell/.env.example`
5. Run `make verify` to confirm conformance

### Three-layer architecture

- Layer 1: Chezmoi templates (`dot_*`, `private_*`) define static machine state.
- Layer 2: Chezmoi scripts bridge templates to runtime generation.
- Layer 3: Configure scripts (`scripts/configure-*.py`, `configure-all.sh`) generate runtime-dependent config.
- Project-scoped Layer 3 configuration uses `scripts/configure-project.py` with
  `.opencode/.env`; generated secrets go to `.opencode/.env.local`.
- Canonical reference: [docs/ORCHESTRATION.md](docs/ORCHESTRATION.md).
- `scripts/configure-acp-agents.py` follows the `configure-*.py` convention and is invoked by `scripts/configure-opencode.py` during OpenCode generation (after the slim config copy, before tier switching). It is gated by `DOTFILES_RUN_OPENCODE_SETUP`, writes gitignored `configs/opencode/acp-agents.json`, and its hash trigger should cover the script itself rather than the generated output to avoid circular reruns. ACP agent verification and the Tokenscope plugin are documented in [docs/OPENCODE.md](docs/OPENCODE.md).

### Environment Gating

Scripts that should be toggleable check `DOTFILES_RUN_*_SETUP` env vars:

```bash
if [[ "${DOTFILES_RUN_WHATEVER_SETUP:-0}" != "1" ]]; then
  info "DOTFILES_RUN_WHATEVER_SETUP='${DOTFILES_RUN_WHATEVER_SETUP:-0}' — skipping"
  exit 0
fi
```

Default is 0 (skip). Set to 1 in `~/.env` to enable.

### Logging & Output Standardization

To ensure clean, prefix-continuous, and readable logs:
- Avoid noisy, verbose line dividers like `============================================` or unnecessary trailing blank `print()` calls.
- Summary blocks or multi-line messages must be aggregated and logged in a single call to preserve prefix consistency and prevent output formatting breaks.
- In Python, compile summary lines into an array and log them with `\n`:
  ```python
  summary_lines = [
      "OpenCode configured!",
      "",
      f"Config written to: {config_dir_path}",
      f"  • opencode.json (providers, MCP servers, plugins)"
  ]
  logger.info("\n".join(summary_lines))
  ```
- In Bash, log multi-line text blocks in a single `info` or `ok` call:
  ```bash
  info "OpenCode tools installed!

  Next steps:
    1. Write config:       configure-opencode.py

  Install script complete!"
  ```

### Style Rules

- **Indent:** 2-space (not tabs), enforced by `.editorconfig` + `shfmt`
- **Lint:** `shellcheck`, `shfmt`, `pre-commit` (local/offline `make lint`), `black`, JSON/YAML/Large files validation via `Makefile`
- **Homebrew-agnostic paths:** Always use `$(brew --prefix)` — never hardcode `/usr/local` or `/opt/homebrew`
- **Network resilience:** Never `set -e` on network calls; use `|| warn` pattern
- **Idempotency:** All `run_once_*` scripts must be safe to re-run
- **Env vars:** `DOTFILES_` prefix for dotfiles-system toggles, `DOTFILES_RUN_*` for script gates
- **Commits:** `feat/fix/refactor/chore/docs` with scopes: `dotfiles`, `brew`, `secrets`, `scripts`, `templates`, `infra`, `agents`

---

## Model Tiers

Tier presets are defined in `configs/opencode/oh-my-opencode-slim.json` (source of truth), consumed via `scripts/lib/tier_registry.py`. For the full tier table, per-tier role/variant tables, local model classification, and fallback chains, see [docs/TIERS.md](docs/TIERS.md). Switch with: `scripts/configure-opencode-tier.py --preset <tier>`.

---

## Secrets Management

`~/.env` is the single source of truth for all secrets. Format: `KEY='VALUE'`. Templates use `{{ env "VAR" }}` syntax.

Prefer Makefile targets (`make diff`, `make dry-run`, `make deploy`) because they load `~/.env` in the same shell process as chezmoi. Use `make drift` to report drift and `make migrate` to append newly documented keys.

No age/GPG encryption is used because this is a local-only repository. Never commit secrets.

Key template files: `dot_gitconfig.tmpl` (GIT_AUTHOR_*, GPG_SIGNING_KEY, GITHUB_USER, GH_TOKEN), `private_dot_npmrc.tmpl` (NPM_TOKEN, GH_TOKEN), `private_dot_aws/credentials.tmpl` (AWS_*), `private_dot_vuescanrc.tmpl` (VUESCAN_*), `private_dot_gnupg/gpg.conf.tmpl` (GPG_SIGNING_KEY).

---

## AI Agent Guidance Files

- `AGENTS.md` (this file) is repo-level guidance for agents working on the dotfiles repo itself. It is in `.chezmoiignore` and is NOT deployed to `~/AGENTS.md`.
- `configs/agents/home-agents.md` is the source of truth for home-level agent guidance. The script `configure-agent-guidance.py` distributes it to `~/AGENTS.md` and all configured agent locations: `~/.claude/CLAUDE.md`, `~/.gemini/GEMINI.md`, `~/.codex/AGENTS.md`, `~/.config/opencode/AGENTS.md`, `~/.cursor/AGENTS.md`, `~/.ai/AGENTS.md` (resolving `~/.junie` symlink), `~/.copilot/copilot-instructions.md`, `~/.pi/agent/AGENTS.md`, `~/.snowflake/cortex/AGENTS.md`, and `~/Documents/Cline/Rules/AGENTS.md`. `configs/agents/repo-agents-shared.md` is stamped into opted-in repository `AGENTS.md` files with `--repo PATH`.
- Deep reference material lives in `docs/` (linked throughout this file).

When editing home-level agent guidance, edit `configs/agents/home-agents.md` first, then run `scripts/configure-agent-guidance.py` to distribute. For shared repo-level guidance, edit `configs/agents/repo-agents-shared.md` first, then run `make stamp-repo-guidance REPO_PATH=/path/to/repo`; keep repository-specific guidance below its markers. All cross-references should link to `docs/` files.

---

## Common Tasks

Run `make help` for the full command list. For task-specific guidance, see the relevant `docs/` reference.

- Switch AI tier: `scripts/configure-opencode-tier.py --preset <tier>`
- Apply all dotfiles: `make deploy`
- Full verification: `make verify`
- Check hash coverage: `make check-hashes`
- Configure a project: `scripts/configure-project.py [--steps skills]`

Project skills are defined in category files under `configs/skills/`; the canonical
cache is `~/.local/share/dotfiles/skills/`. See
[docs/ORCHESTRATION.md](docs/ORCHESTRATION.md) for the full workflow.

---

## Deepwork Progress Files

Deepwork sessions keep progress state in `.slim/deepwork/<topic>.md` (git-local, OpenCode-readable via `.ignore`). Conventions:

- Keep **one mutable Status line** at the top of the file; update it in place instead of appending duplicate status lines.
- Phase logs are append-only history; when a session closes, the Status line is the single source of truth for "where did this leave off."
- `.slim/deepwork/` is strictly progress state — deliverables go to project paths.

## Tone and Style

Reply in the language and register the conversation is using. English prose
uses Canadian English spelling; formal reports and reviews follow Canadian
Press style (casual conversation stays casual). Full guidance lives in
`configs/agents/home-agents.md` (distributed by `configure-agent-guidance.py`).

## Reference Docs Index

| Doc | Content |
|-----|---------|
| [docs/CONVENTIONS.md](docs/CONVENTIONS.md) | CLI capability contract, arg forwarding, env-var taxonomy, cross-language parity, enforcement |
| [docs/TELEMETRY.md](docs/TELEMETRY.md) | Per-tool telemetry opt-out dispositions, env var reference, fleet registry |
| [docs/TIERS.md](docs/TIERS.md) | Tier definitions, per-tier role/variant tables, local model classification, fallback chains, variant policy, Ollama Cloud models |
| [docs/MODEL_UPDATES.md](docs/MODEL_UPDATES.md) | Model update and registry maintenance guidance |
| [docs/MOZART.md](docs/MOZART.md) | Mozart router gateways, unified Ollama routing, provider overrides, JSON config convention |
| [docs/MERIDIAN.md](docs/MERIDIAN.md) | Meridian proxy, SDK feature toggles, Sonnet context tier, OpenCode/Meridian context sync |
| [docs/VOICE.md](docs/VOICE.md) | Voice plugin, tier-aware STT/TTS, dependencies, model defaults, config locations |
| [docs/JUNIE.md](docs/JUNIE.md) | Junie model groups ↔ Oh My OpenCode sync, mapping rules, temperature overrides, deployment |
| [docs/MULTIPLEXER.md](docs/MULTIPLEXER.md) | tmux/zellij side-by-side editing with OpenCode, configuration, launching, prerequisites |
| [docs/DCP.md](docs/DCP.md) | Context compaction thresholds, OpenCode config paths |
| [docs/ADDING.md](docs/ADDING.md) | Adding a new tier, an MCP server, configure script, gate, or hash trigger |
| [docs/ORCHESTRATION.md](docs/ORCHESTRATION.md) | Three-layer architecture, script inventory, gates, dependency ordering, troubleshooting |
| [docs/INSTALL.md](docs/INSTALL.md) | Full installation, upgrade, and verification instructions |
| [docs/CADDY.md](docs/CADDY.md) | Caddy, LAN exposure, certificates, and Plannotator integration |
| [docs/PI.md](docs/PI.md) | Pi terminal coding agent, providers, MCP, ACP, and skills |
| [docs/OPENCODE.md](docs/OPENCODE.md) | OpenCode configuration, ACP agent verification, Tokenscope plugin |
| [docs/AGENTIC-REVIEW.md](docs/AGENTIC-REVIEW.md) | Agentic PR review GitHub Actions (OpenCode/Junie/Gemini/Copilot), labels, mentions, CI MCP, codegraph caching, free preset |
| [docs/CORTEX.md](docs/CORTEX.md) | Snowflake Cortex Code specialist, MCP, ACP, and skills |
| [docs/CAPABILITY_MATRIX.md](docs/CAPABILITY_MATRIX.md) | Per-tool providers, MCP, ACP, skills, presets, guidance, Meridian, and local fallback support |

---

For human-facing commands, package management, platform notes, and component-specific reference, see [README.md](README.md) and the linked `docs/` files above.
