#!/usr/bin/env bash
set -euo pipefail

_SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
# shellcheck source=./scripts/lib/common.sh
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/common.sh"

usage() {
  printf 'Usage: %s [--dry-run]\n' "$(basename "$0")"
  printf '\nInstall codegraph and initialize or sync the local index.\n'
}

dry_run=false
while (($# > 0)); do
  case "$1" in
  --help | -h)
    usage
    exit 0
    ;;
  --dry-run)
    dry_run=true
    ;;
  *)
    printf 'Unknown option: %s\n' "$1" >&2
    usage >&2
    exit 2
    ;;
  esac
  shift
done

if [[ "$dry_run" == "true" ]]; then
  info "Dry run: would install codegraph if needed and initialize or sync .codegraph."
  exit 0
fi

if command -v codegraph >/dev/null 2>&1; then
  info "Using existing codegraph: $(codegraph --version 2>&1 || true)"
  codegraph_cmd=(codegraph)
elif command -v npm >/dev/null 2>&1; then
  info "Installing codegraph with npm."
  npm install -g @colbymchenry/codegraph
  codegraph_cmd=(codegraph)
else
  die "codegraph is not on PATH and npm is unavailable; install codegraph or npm for the PATH-based MCP server."
fi

if ! command -v codegraph >/dev/null 2>&1; then
  die "codegraph installation completed but the binary is not on PATH."
fi

if [[ -f .codegraph/codegraph.db ]]; then
  "${codegraph_cmd[@]}" sync || {
    warn "codegraph sync failed; attempting a full index rebuild."
    "${codegraph_cmd[@]}" index
  }
else
  "${codegraph_cmd[@]}" init --yes
fi

info "Codegraph setup complete.

Index: .codegraph/codegraph.db
MCP: codegraph serve --mcp"
