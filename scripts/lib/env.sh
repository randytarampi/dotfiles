#!/usr/bin/env bash

# Environment helper functions

load_env() {
  local env_file="${1:-$HOME/.env}"
  if [[ -f "$env_file" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "$env_file"
    set +a
    return 0
  else
    return 1
  fi
}

alias_github_token() {
  local gh_token="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
  if [[ -n "$gh_token" ]]; then
    export GH_TOKEN="$gh_token"
    export GITHUB_TOKEN="$GH_TOKEN"
  fi
}
