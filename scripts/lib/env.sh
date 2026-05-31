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
  if [[ -n "${GITHUB_TOKEN:-}" && -z "${GH_TOKEN:-}" ]]; then
    export GH_TOKEN="$GITHUB_TOKEN"
  elif [[ -z "${GITHUB_TOKEN:-}" && -n "${GH_TOKEN:-}" ]]; then
    export GITHUB_TOKEN="$GH_TOKEN"
  fi
}
