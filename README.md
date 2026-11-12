# Dotfiles

```text
eeeeee eeeee eeeee eeee e  e     eeee eeeee
8   8 8  88   8   8    8  8     8    8   "
8e  8 8   8   8e  8eee 8e 8e    8eee 8eeee
88  8 8   8   88  88   88 88    88      88
88ee8 8eee8   88  88   88 88eee 88ee 8ee88
```

[![CI](https://img.shields.io/github/actions/workflow/status/randytarampi/dotfiles/ci.yml?branch=main&style=flat-square&label=CI)](https://github.com/randytarampi/dotfiles/actions/workflows/ci.yml) [![CodeQL](https://img.shields.io/github/actions/workflow/status/randytarampi/dotfiles/codeql.yml?branch=main&style=flat-square&label=CodeQL)](https://github.com/randytarampi/dotfiles/security/code-scanning) [![Coverage](https://img.shields.io/coveralls/randytarampi/dotfiles.svg?branch=main&style=flat-square)](https://coveralls.io/github/randytarampi/dotfiles?branch=main) [![License](https://img.shields.io/badge/license-unlicense-blue?style=flat-square)](LICENSE)

Personal, cross-platform dotfiles managed by [chezmoi](https://chezmoi.io/). The repository manages shell/editor configuration, package manifests, AI coding tools, model routing, MCP integrations and selected local services. Individual integrations are opt-in; committing a configuration does not mean it has been installed or qualified on every host.

## Getting started

Clone the repository, follow the [installation guide](docs/install.md) to seed your private `~/.env`, install prerequisites (including Poetry for managed Python tools), and initialise chezmoi. On a prepared macOS/Linux host:

```bash
make deploy
make deploy  # First setup only: check idempotency
```

The Makefile assumes Bash and Unix utilities. Windows uses winget/PowerShell for package installation but requires an appropriately prepared POSIX-compatible environment for Makefile targets; do not assume every target runs natively in PowerShell. See [installation and upgrading](docs/install.md) for the platform-specific sequence and recovery steps.

**Secrets and user state:** Never commit credentials. The canonical environment example is [dot_dotfiles/shell/.env.example](dot_dotfiles/shell/.env.example); `~/.env` and runtime-generated credentials remain private. Gates default to off unless explicitly documented otherwise. Run a dry-run before changing an unfamiliar deployment.

## Common commands

| Command | Purpose |
| --- | --- |
| `make help` | Show available targets |
| `make diff` / `make dry-run` | Preview chezmoi changes without applying them |
| `make deploy` | Apply managed dotfiles and run configuration orchestration |
| `make configure` | Regenerate runtime configuration without a chezmoi apply |
| `make doctor` | Inspect deployed configuration/host drift |
| `make verify` | Run the integrated validation suite (may require host tooling and services) |
| `make ci-verify` | Run the CI-oriented verification subset |
| `make drift` / `make migrate-preview` | Inspect private environment drift and proposed gate migration |
| `make check-hashes` / `make check-cli-contract` | Check deployment inputs and public script interfaces |

Run `make help` for the current list, rather than relying on a copied list of scripts or flags. `make verify` does not substitute for actual installed-client, authenticated-inference or live-service acceptance.

## How the repository is organised

| Source | Responsibility |
| --- | --- |
| `dot_*`, `private_*` and `.chezmoi.toml.tmpl` | Home files and chezmoi templates |
| `.chezmoiscripts/` | Opt-in installation/deployment lifecycle and hash-triggered updates |
| `scripts/` and `scripts/lib/` | Orchestration, generators, shared models, checks and tests |
| `configs/opencode/oh-my-opencode-slim.json` | Shared AI role/preset selection |
| `configs/junie/model-groups.json` | Junie-specific model groups and provider metadata |
| `configs/mcp/global-mcps.json` | Canonical MCP registry and per-tool consumers |
| `configs/opencode/opencode-plugins.json` | Managed OpenCode plugins and TUI-only plugins |
| `configs/agents/` and `AGENTS.md` | Distributed and repository-specific agent guidance |
| `Brewfile*`, `wingetfile*`, `.chezmoidata/categories.yaml` | Package categories and platform manifests |
| `docs/` | Maintained installation, operating and integration guidance |

AI clients share policy and discovery inputs where supported, but each has its own configuration generator and transport contract. A listed model or provider is not evidence of credential access, tool-call support, free entitlement or live readiness. Consult the active manifests and corresponding tests before changing defaults.

## Documentation by task

| Task | Canonical guide |
| --- | --- |
| Install, upgrade or troubleshoot the host | [Installation](docs/install.md) and [orchestration](docs/orchestration.md) |
| Add scripts, gates, MCP tools or skills | [Adding components](docs/adding.md) and [repository conventions](docs/conventions.md) |
| Understand AI presets and model updates | [Tiers](docs/tiers.md), [model updates](docs/model-updates.md), [capability matrix](docs/capability-matrix.md) |
| Configure OpenCode, Pi, Junie or other clients | [OpenCode](docs/opencode.md), [Pi](docs/pi.md), [Junie](docs/junie.md), [unmanaged clients](docs/unmanaged-clients.md) |
| Configure additional inference providers or the gateway | [Optional inference](docs/optional-inference.md), [LiteLLM](docs/litellm.md), [Meridian](docs/meridian.md) |
| Configure local engines, voice or memory | [Ollama headless package](docs/ollama-service.md), [oMLX](docs/omlx.md), [voice](docs/voice.md), [Basic Memory](docs/basic-memory.md) |
| Configure remote access and web services | [Remote access](docs/remote-access.md), [Caddy](docs/caddy.md), [Open WebUI](docs/chat-frontend.md) |
| Configure Paseo | [Paseo remote](docs/paseo-remote.md), [security](docs/paseo-security.md), [ACP](docs/paseo-acp.md), [MCP](docs/paseo-mcp.md) |
| Work on agentic PR reviews | [GitHub reviews and onboarding](docs/agentic-review.md), [review method](configs/review/code-review-prompt.md) |

[AGENTS.md](AGENTS.md) is the authoritative instruction source for contributions to this repository, and [home agent guidance](configs/agents/home-agents.md) is the source distributed to supported user agents. The documentation is a description of repo intent and implementation, not proof of an installed host's state.

## Packaging and platforms

The package manifests are category-based. `.chezmoidata/categories.yaml` defines committed defaults, and optional machine-level category overrides belong in chezmoi's local configuration rather than edits to shared defaults. Use `make categories` to inspect the effective selection and `make brewfile-sync` / `make brewfile-diff` when maintaining package parity. Some packages or services are platform-specific, and a Brewfile entry does not imply an equivalent winget package.

OpenCode v2 is managed by its [official installer path](docs/opencode.md), not a Brewfile or winget package. Tool-specific gates, supported platforms, security boundaries and required owner actions belong in the linked operating guides, not in a manually duplicated package/model matrix here.

## Contributing and validation

Follow [AGENTS.md](AGENTS.md), the [PR template](.github/PULL_REQUEST_TEMPLATE.md) and the [review rubric](configs/review/code-review-prompt.md). Run the relevant focused checks and record results against the actual revision; code/configuration changes require the integrated verification gate when a qualifying environment is available. Agent-created PRs are drafts until accepted by a human reviewer.

Markdown prose should **soft-wrap in the editor** rather than being hard-wrapped at an arbitrary width. Preserve intentional Markdown structure, code blocks and hard breaks; see [documentation conventions](docs/conventions.md).
