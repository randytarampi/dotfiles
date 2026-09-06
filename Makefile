.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST) | sort

.PHONY: lint fix env drift migrate stamp-repo-guidance check-repo-guidance brewfile-sync brewfile-diff brewfile-cleanup categories diff dry-run deploy configure doctor check-hashes check-ci-assets check-env-coverage check-cli-contract check-fleet-coverage check-pep604 check-categories check-slim-invariants check-model-drift check-templates check-plugin-consistency check-actionlint verify reset symlinks test test-tier-registry caddy-deploy caddy-validate caddy-reload caddy-migrate opencode-start opencode-stop opencode-restart plannotator-restart meridian-restart ddns-restart caddy-restart ollama-env-restart services-restart skills-update codegraph clean-backups project-cleanup

SHELL := /usr/bin/env bash
CHEZMOI ?= chezmoi
CHEZMOI_SOURCE := $(CURDIR)
ENV_FILE ?= $(HOME)/.env
ENV_EXAMPLE ?= dot_dotfiles/shell/.env.example
SHFMT ?= $(shell if command -v shfmt >/dev/null 2>&1; then command -v shfmt; elif command -v brew >/dev/null 2>&1 && [ -x "$$(brew --prefix)/bin/shfmt" ]; then printf "%s/bin/shfmt" "$$(brew --prefix)"; fi)
PYTHON := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

define LOAD_ENV
set -a; [ -f "$(ENV_FILE)" ] && . "$(ENV_FILE)"; set +a
endef

lint: ## Run repository lint and syntax checks
	@echo "Checking for merge conflicts..."
	@git grep -n '^<<<<<<<' -- '*.*' 2>/dev/null && { echo "Merge conflict markers found!"; exit 1; } || true
	@echo "Checking for trailing whitespace..."
	@git grep -I -n '[[:space:]]$$' -- '*.*' 2>/dev/null && { echo "Trailing whitespace found!"; exit 1; } || true
	@echo "Checking for large files (> 500KB)..."
	@large_files=$$(find . -type f -not -path '*/.*' -not -path './node_modules/*' -not -path './.venv/*' -not -path './bin/*' -size +500k 2>/dev/null); \
	if [ -n "$$large_files" ]; then \
		echo "Large files found (> 500KB):"; \
		echo "$$large_files"; \
		exit 1; \
	fi
	@echo "Running shellcheck..."
	@shellcheck --severity=warning --exclude=SC1090,SC1091,SC2148 scripts/*.sh scripts/lib/*.sh dot_dotfiles/shell/*.sh
	@echo "Checking shell formatting with shfmt..."
	@if [ -n "$(SHFMT)" ]; then \
		"$(SHFMT)" -i 2 -d scripts/*.sh scripts/lib/*.sh dot_dotfiles/shell/*.sh; \
	else \
		echo "shfmt not found. Install it with 'brew install shfmt' or 'winget install mvdan.shfmt'."; \
		exit 1; \
	fi
	@echo "Checking JSON file syntax..."
	@for f in configs/mcp/*.json configs/mcp/templates/*.json configs/iterm2/*.json configs/junie/*.json configs/opencode/*.json dot_dotfiles/shell/*.json; do \
		if [ -f "$$f" ]; then \
			python3 -m json.tool "$$f" > /dev/null || exit 1; \
		fi; \
	done
	@echo "Checking YAML file syntax..."
	@if command -v js-yaml >/dev/null 2>&1; then \
		for f in *.yaml *.yml .chezmoidata/*.yaml .chezmoidata/*.yml; do \
			if [ -f "$$f" ]; then \
				js-yaml "$$f" > /dev/null || exit 1; \
			fi; \
		done; \
	else \
		echo "js-yaml not installed; skipping YAML check"; \
	fi
	@echo "Checking Python syntax compiler-checks..."
	@for f in scripts/*.py scripts/lib/*.py; do [ -f "$$f" ] && python3 -m py_compile "$$f" >/dev/null || exit 1; done
	@echo "Checking PEP 604 type hint compatibility..."
	@python3 scripts/check-pep604.py
	@echo "Checking Python formatting with black (dry-run)..."
	@if command -v black >/dev/null 2>&1; then \
		black --check scripts/ scripts/lib/; \
	else \
		echo "black not installed; skipping Python format check"; \
	fi

fix: ## Format shell and Python files and normalize text files
	@if [ -n "$(SHFMT)" ]; then \
		"$(SHFMT)" -i 2 -w scripts/*.sh scripts/lib/*.sh dot_dotfiles/shell/*.sh; \
	else \
		echo "shfmt not found. Install it with 'brew install shfmt' or 'winget install mvdan.shfmt'."; \
		exit 1; \
	fi
	@if command -v black >/dev/null 2>&1; then \
		black scripts/ scripts/lib/; \
		chmod +x scripts/*.py; \
	else \
		echo "black not installed; skipping python formatting"; \
	fi
	@echo "Stripping trailing whitespaces and fixing EOF newlines..."
	@python3 -c 'exec("import sys, glob, os\n" \
	"for ext in [\"*.py\", \"*.sh\", \"*.json\", \"*.tmpl\", \"*.yml\", \"*.yaml\", \"*.md\"]:\n" \
	"    for root, dirs, files in os.walk(\".\"):\n" \
	"        if \"node_modules\" in root or \".git\" in root or \".venv\" in root: continue\n" \
	"        for f in glob.glob(os.path.join(root, ext)):\n" \
	"            if not os.path.isfile(f): continue\n" \
	"            try:\n" \
	"                with open(f, \"rb\") as file: content = file.read()\n" \
	"                lines = content.splitlines()\n" \
	"                new_lines = [line.rstrip() for line in lines]\n" \
	"                new_content = b\"\\n\".join(new_lines)\n" \
	"                if new_content and not new_content.endswith(b\"\\n\"): new_content += b\"\\n\"\n" \
	"                if new_content != content:\n" \
	"                    with open(f, \"wb\") as file: file.write(new_content)\n" \
	"                    print(\"Fixed \" + f)\n" \
	"            except Exception: pass\n")'

env: ## Load ~/.env for this make recipe only
	@$(LOAD_ENV); echo "Loaded $(ENV_FILE) for this make recipe only."

drift: ## Check ~/.env drift against .env.example (read-only)
	@python3 scripts/sync-env.py --example "$(ENV_EXAMPLE)" --env "$(ENV_FILE)" --check

stamp-repo-guidance: ## Stamp shared guidance into REPO_PATH/AGENTS.md
	@test -n "$(REPO_PATH)" || { echo "REPO_PATH is required"; exit 2; }
	@python3 scripts/configure-agent-guidance.py --repo "$(REPO_PATH)"

check-repo-guidance: ## Check shared guidance drift in REPO_PATH/AGENTS.md
	@test -n "$(REPO_PATH)" || { echo "REPO_PATH is required"; exit 2; }
	@python3 scripts/configure-agent-guidance.py --repo "$(REPO_PATH)" --check

migrate: ## Migrate deprecated DOTFILES_RUN_* gate names in ~/.env to current names, then sync new keys
	@python3 scripts/migrate-env-gates.py --env "$(ENV_FILE)"
	@python3 scripts/sync-env.py --example "$(ENV_EXAMPLE)" --env "$(ENV_FILE)" --sync

brewfile-sync: ## Synchronize Brewfile and Wingetfile package manifests
	@python3 scripts/sync-brewfiles.py

brewfile-diff: ## Show package manifest differences
	@python3 scripts/sync-brewfiles.py --diff

brewfile-cleanup: ## Remove stale package manifest entries
	@python3 scripts/cleanup-brewfiles.py

clean-backups: ## Remove stale .bak files from ~/.config/opencode/
	@echo "Removing backup files from ~/.config/opencode/..."
	@rm -f ~/.config/opencode/*.bak

project-cleanup: ## Remove configure-project.py generated artifacts from a project (run from project root, or set PROJECT_ROOT)
	@python3 scripts/cleanup-project.py --workspace-root "$(PROJECT_ROOT)" $(if $(filter 1,$(FORCE)),--force) $(if $(filter 1,$(DRY_RUN)),--dry-run)
	@echo "Done."

categories: ## Show per-machine category state and toggle overrides
	@python3 scripts/show-categories.py

diff: ## Show pending chezmoi changes
	@$(LOAD_ENV); $(CHEZMOI) --source "$(CHEZMOI_SOURCE)" diff

dry-run: ## Preview chezmoi changes without applying them
	@$(LOAD_ENV); $(CHEZMOI) --source "$(CHEZMOI_SOURCE)" apply --dry-run --force

deploy: ## Apply dotfiles and run all configuration scripts
	@$(LOAD_ENV); $(CHEZMOI) --source "$(CHEZMOI_SOURCE)" apply --force
	@$(LOAD_ENV); bash scripts/configure-all.sh
	@python3 -c "import sys; sys.path.insert(0,'scripts/lib'); from model_stamp import notice_message; message = notice_message(); print(message) if message else None"

configure: ## Run all AI tool configure scripts (without chezmoi apply)
	@$(LOAD_ENV); bash scripts/configure-all.sh

doctor: ## Read-only drift checks: verify generated configs exist
	@$(LOAD_ENV); python3 scripts/verify-config.py

check-hashes: ## Verify hash trigger coverage in run_onchange scripts
	@python3 scripts/check-hashes.py

check-ci-assets: ## Verify CI/local-only asset hashes
	@python3 scripts/verify-ci-assets.py

update-ci-assets: ## Regenerate configs/review/assets-manifest.json after editing CI/local-only assets
	@python3 -c "import json, hashlib; from pathlib import Path; root = Path('.'); assets = ['configs/review/code-review-prompt.md', 'scripts/run-local-review.sh', 'scripts/ci-codegraph.sh', 'configs/opencode/ci/opencode.json', 'scripts/onboard-agentic-review.py']; manifest = {'assets': {a: hashlib.sha256((root / a).read_bytes()).hexdigest() for a in assets}}; Path('configs/review/assets-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')"
	@python3 scripts/verify-ci-assets.py

check-env-coverage: ## Verify DOTFILES_* env vars are documented in .env.example
	@python3 scripts/check-env-coverage.py

check-cli-contract: ## Verify CLI interfaces match the capability manifest
	@python3 scripts/check-cli-contract.py

check-fleet-coverage: ## Verify fleet telemetry and guidance coverage
	@python3 scripts/check-fleet-coverage.py

check-pep604: ## Check for PEP 604 type hints without future annotations import
	@python3 scripts/check-pep604.py

check-categories: ## Verify Brewfile/wingetfile category registries are in sync
	@python3 scripts/check-categories.py

check-slim-invariants: ## Verify oh-my-opencode-slim fallback arrays have no dupes
	@python3 scripts/verify-slim-invariants.py

check-model-drift: ## Check deployed model configs against live catalogs
	@python3 scripts/check-model-drift.py

check-templates: ## Render JSON chezmoi templates and validate output (catches Go template errors that lint misses)
	@echo "Rendering and validating JSON templates..."
	@set -e; \
	for tmpl in configs/iterm2/Default.json.tmpl dot_config/plannotator/config.json.tmpl; do \
		if [ -f "$$tmpl" ]; then \
			echo "  Rendering $$tmpl..."; \
			rendered=$$(cat "$$tmpl" | $(CHEZMOI) execute-template 2>&1) || { \
				echo "ERROR: $$tmpl failed to render:"; \
				echo "$$rendered"; \
				exit 1; \
			}; \
			if [ -z "$$rendered" ]; then \
				echo "ERROR: $$tmpl rendered to empty output"; \
				exit 1; \
			fi; \
			echo "$$rendered" | python3 -m json.tool > /dev/null || { \
				echo "ERROR: $$tmpl rendered invalid JSON"; \
				exit 1; \
			}; \
		fi; \
	done

check-docs-drift: ## Check for documentation drift between AGENTS.md, README.md, and docs/
	@python3 scripts/check-docs-drift.py

check-plugin-consistency: ## Verify plugin arrays match between install script and config generator
	@python3 scripts/check-plugin-consistency.py

verify-iterm2: ## Verify iTerm2 config integrity (JSON, template, paths, writability)
	@python3 scripts/verify-iterm2.py

verify: lint drift check-hashes check-ci-assets check-env-coverage check-cli-contract check-fleet-coverage check-pep604 check-categories check-slim-invariants check-model-drift check-templates check-docs-drift check-plugin-consistency verify-iterm2 check-actionlint test-tier-registry doctor dry-run ## Full verification suite
	@echo "All checks passed."

.PHONY: ci-verify
ci-verify: lint drift doctor check-hashes check-env-coverage check-cli-contract check-fleet-coverage check-pep604 check-categories check-slim-invariants check-templates check-docs-drift check-plugin-consistency verify-iterm2 ## Run CI verification checks
	@echo "CI verification complete."

reset: ## Clear chezmoi script state (forces re-run of all scripts on next deploy)
	@$(CHEZMOI) state delete-bucket --bucket scriptState
	@echo "Script state cleared. Run 'make deploy' to re-run all scripts."

symlinks: ## Create symlinks for repository scripts
	@bash scripts/setup-bin-symlinks.sh "$(CURDIR)/scripts"

test: ## Run the Python test suite
	@PYTHONPATH=scripts/lib $(PYTHON) -m pytest scripts/lib/tests/ -q

test-tier-registry: ## Run tier registry unit tests
	@PYTHONPATH=scripts/lib $(PYTHON) -m pytest scripts/lib/tests/test_tier_registry.py -q

check-actionlint: ## Lint all GitHub Actions workflows
	@command -v actionlint >/dev/null 2>&1 || { echo "actionlint not installed (brew install actionlint)"; exit 1; }
	@actionlint .github/workflows/*.yml

caddy-migrate: ## One-time: decommission existing dedicated-user acme/ddns setup
	@bash scripts/migrate-acme-ddns.sh

caddy-deploy: ## Generate Caddyfile and restart Caddy (root LaunchDaemon)
	@$(LOAD_ENV); python3 scripts/configure-caddy.py
	@rm -f "$(HOME)/Library/Application Support/Caddy/autosave.json" 2>/dev/null || true
	@sudo launchctl bootout system/com.caddy.proxy 2>/dev/null || true
	@sleep 2
	@sudo launchctl bootstrap system /Library/LaunchDaemons/com.caddy.proxy.plist

caddy-validate: ## Validate Caddyfile syntax
	@caddy validate --config "$$(brew --prefix)/etc/caddy/Caddyfile" || echo "Caddyfile validation failed (is Caddy installed?)"

caddy-reload: ## Hot-reload Caddy config
	@sudo "$$(brew --prefix)/bin/caddy" reload --force --config "$$(brew --prefix)/etc/caddy/Caddyfile" || echo "Caddy reload failed"

caddy-restart: ## Restart Caddy service (system-level)
	@if [ "$$(uname)" = "Darwin" ]; then \
		sudo launchctl bootout system/com.caddy.proxy 2>/dev/null || true; \
		sleep 2; \
		sudo launchctl bootstrap system/ /Library/LaunchDaemons/com.caddy.proxy.plist 2>/dev/null || true; \
	elif [ "$$(uname)" = "Linux" ]; then \
		sudo systemctl restart caddy 2>/dev/null || true; \
	fi
	@echo "Caddy restarted."

# ─── Service management ───────────────────────────────────────────────────────
# OpenCode Web does not pick up config changes automatically — restart is
# required after any config regeneration (opencode.json, oh-my-opencode-slim.json,
# acp-agents.json, etc.).

opencode-start: ## Start OpenCode Web service
	@echo "Starting OpenCode Web..."
	@if [ "$$(uname)" = "Darwin" ]; then \
		launchctl bootstrap "gui/$$(id -u)" ~/Library/LaunchAgents/com.opencode.web.plist 2>/dev/null || true; \
		launchctl kickstart -k "gui/$$(id -u)/com.opencode.web" 2>/dev/null || true; \
	else \
		systemctl --user start opencode-web 2>/dev/null || true; \
	fi
	@echo "OpenCode Web started."

opencode-stop: ## Stop OpenCode Web service
	@echo "Stopping OpenCode Web..."
	@if [ "$$(uname)" = "Darwin" ]; then \
		launchctl bootout "gui/$$(id -u)/com.opencode.web" 2>/dev/null || true; \
	else \
		systemctl --user stop opencode-web 2>/dev/null || true; \
	fi
	@echo "OpenCode Web stopped."

opencode-restart: opencode-stop opencode-start ## Restart OpenCode Web service
	@echo "OpenCode Web restarted."

meridian-restart: ## Restart Meridian proxy service
	@if [ "$$(uname)" = "Darwin" ]; then \
		launchctl bootout "gui/$$(id -u)/com.meridian.proxy" 2>/dev/null || true; \
		sleep 1; \
		launchctl bootstrap "gui/$$(id -u)" ~/Library/LaunchAgents/com.meridian.proxy.plist 2>/dev/null || true; \
	elif [ "$$(uname)" = "Linux" ]; then \
		systemctl --user restart meridian-proxy 2>/dev/null || true; \
	fi
	@echo "Meridian restarted."

ddns-restart: ## Restart all ddns-route53 services
	@if [ "$$(uname)" = "Darwin" ]; then \
		for plist in ~/Library/LaunchAgents/com.crazymax.ddns-route53.*.plist; do \
			[ -f "$$plist" ] || continue; \
			label=$$(basename "$$plist" .plist); \
			launchctl bootout "gui/$$(id -u)/$$label" 2>/dev/null || true; \
			sleep 1; \
			launchctl bootstrap "gui/$$(id -u)" "$$plist" 2>/dev/null || true; \
		done; \
	elif [ "$$(uname)" = "Linux" ]; then \
		systemctl --user restart 'ddns-route53-*' 2>/dev/null || true; \
	fi
	@echo "DDNS Route53 agents restarted."

ollama-env-restart: ## Re-apply Ollama daemon environment variables
	@if [ "$$(uname)" = "Darwin" ]; then \
		launchctl kickstart -k "gui/$$(id -u)/com.dotfiles.ollama-env" 2>/dev/null || true; \
	elif [ "$$(uname)" = "Linux" ]; then \
		if systemctl list-unit-files 2>/dev/null | grep -q ollama.service; then \
			sudo systemctl restart ollama 2>/dev/null || true; \
		elif systemctl --user list-unit-files 2>/dev/null | grep -q ollama.service; then \
			systemctl --user restart ollama 2>/dev/null || true; \
		fi; \
	fi
	@echo "Ollama env re-applied."

# Plannotator uses a fixed port (19432 for portal, 19433 for paste backend).
# Multiple OpenCode sessions can conflict on the same port. This target
# clears the paste backend port so a fresh session can bind.
plannotator-restart: ## Restart Plannotator paste service
	@echo "Restarting Plannotator..."
	@if [ "$$(uname)" = "Darwin" ]; then \
		lsof -ti:19433 | xargs kill -9 2>/dev/null || true; \
		launchctl bootout "gui/$$(id -u)/com.plannotator.paste" 2>/dev/null || true; \
		sleep 1; \
		launchctl bootstrap "gui/$$(id -u)" ~/Library/LaunchAgents/com.plannotator.paste.plist 2>/dev/null || true; \
	elif [ "$$(uname)" = "Linux" ]; then \
		systemctl --user restart plannotator-paste 2>/dev/null || true; \
	else \
		lsof -ti:19433 | xargs kill -9 2>/dev/null || true; \
	fi
	@echo "Plannotator restarted."

services-restart: opencode-restart plannotator-restart meridian-restart ddns-restart caddy-restart ollama-env-restart ## Restart all services

skills-update: ## Update all skills from upstream via `skills` CLI
	@$(LOAD_ENV); python3 scripts/configure-skills.py --update
	@echo "Skills updated."

codegraph: ## Batch-initialize CodeGraph indexes in all git repos under ~/Development
	@$(LOAD_ENV); python3 scripts/configure-codegraph.py
	@echo "CodeGraph indexes configured."
