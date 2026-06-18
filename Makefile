.PHONY: lint fix env drift migrate brewfile-sync brewfile-diff diff dry-run deploy configure doctor check-hashes verify reset symlinks test

SHELL := /usr/bin/env bash
CHEZMOI ?= chezmoi
CHEZMOI_SOURCE := $(CURDIR)
ENV_FILE ?= $(HOME)/.env
ENV_EXAMPLE ?= dot_dotfiles/shell/.env.example
SHFMT ?= $(shell if command -v shfmt >/dev/null 2>&1; then command -v shfmt; elif command -v brew >/dev/null 2>&1 && [ -x "$$(brew --prefix)/bin/shfmt" ]; then printf "%s/bin/shfmt" "$$(brew --prefix)"; fi)

define LOAD_ENV
set -a; [ -f "$(ENV_FILE)" ] && . "$(ENV_FILE)"; set +a
endef

lint:
	@echo "Checking for merge conflicts..."
	@git grep -n '^<<<<<<<' -- '*.*' 2>/dev/null && { echo "Merge conflict markers found!"; exit 1; } || true
	@echo "Checking for trailing whitespace..."
	@git grep -I -n '[[:space:]]$$' -- '*.*' 2>/dev/null && { echo "Trailing whitespace found!"; exit 1; } || true
	@echo "Checking for large files (> 500KB)..."
	@large_files=$$(find . -type f -not -path '*/.*' -not -path './node_modules/*' -not -path './.venv/*' -size +500k 2>/dev/null); \
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
	@echo "Checking Python formatting with black (dry-run)..."
	@if command -v black >/dev/null 2>&1; then \
		black --check scripts/ scripts/lib/; \
	else \
		echo "black not installed; skipping Python format check"; \
	fi

fix:
	@if [ -n "$(SHFMT)" ]; then \
		"$(SHFMT)" -i 2 -w scripts/*.sh scripts/lib/*.sh dot_dotfiles/shell/*.sh; \
	else \
		echo "shfmt not found. Install it with 'brew install shfmt' or 'winget install mvdan.shfmt'."; \
		exit 1; \
	fi
	@if command -v black >/dev/null 2>&1; then \
		black scripts/ scripts/lib/; \
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

env:
	@$(LOAD_ENV); echo "Loaded $(ENV_FILE) for this make recipe only."

drift: ## Check ~/.env drift against .env.example (read-only)
	@python3 scripts/sync-env.py --example "$(ENV_EXAMPLE)" --env "$(ENV_FILE)" --check

migrate: ## Migrate deprecated DOTFILES_RUN_* gate names in ~/.env to current names, then sync new keys
	@python3 scripts/migrate-env-gates.py --env "$(ENV_FILE)"
	@python3 scripts/sync-env.py --example "$(ENV_EXAMPLE)" --env "$(ENV_FILE)" --sync

brewfile-sync:
	@python3 scripts/sync-brewfiles.py

brewfile-diff:
	@python3 scripts/sync-brewfiles.py --diff

diff:
	@$(LOAD_ENV); $(CHEZMOI) --source "$(CHEZMOI_SOURCE)" diff

dry-run:
	@$(LOAD_ENV); $(CHEZMOI) --source "$(CHEZMOI_SOURCE)" apply --dry-run

deploy: ## Apply dotfiles and run all configuration scripts
	@$(LOAD_ENV); $(CHEZMOI) --source "$(CHEZMOI_SOURCE)" apply
	@$(LOAD_ENV); bash scripts/configure-all.sh

configure: ## Run all AI tool configure scripts (without chezmoi apply)
	@$(LOAD_ENV); bash scripts/configure-all.sh

doctor: ## Read-only drift checks: verify generated configs exist
	@$(LOAD_ENV); python3 scripts/verify-config.py

check-hashes: ## Verify hash trigger coverage in run_onchange scripts
	@python3 scripts/check-hashes.py

verify: lint drift doctor check-hashes dry-run ## Full verification suite
	@echo "All checks passed."

reset: ## Clear chezmoi script state (forces re-run of all scripts on next deploy)
	@$(CHEZMOI) state delete-bucket --bucket scriptState
	@echo "Script state cleared. Run 'make deploy' to re-run all scripts."

symlinks:
	@bash scripts/setup-bin-symlinks.sh "$(CURDIR)/scripts"

test: lint drift dry-run
	@echo "All basic checks passed."
