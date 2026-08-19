#!/usr/bin/env bash
set -euo pipefail

_SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
LIB_DIR="$SCRIPT_DIR/lib"
source "$LIB_DIR/common.sh"
source "$LIB_DIR/common_args.sh"
export COMMON_USAGE="$0"
export COMMON_HELP_TEXT="Install the Snowflake Cortex Code CLI."
export COMMON_STRICT=1
parse_common_args "$@"

if [[ "${DOTFILES_RUN_CORTEX_SETUP:-0}" != "1" ]]; then
  info "DOTFILES_RUN_CORTEX_SETUP='${DOTFILES_RUN_CORTEX_SETUP:-0}' — skipping Cortex install"
  exit 0
fi

SNOWFLAKE_HOME="${SNOWFLAKE_HOME:-$HOME/.snowflake}"
if [[ ! -f "$SNOWFLAKE_HOME/connections.toml" ]] || ! command -v snow >/dev/null 2>&1; then
  info "Cortex requires $SNOWFLAKE_HOME/connections.toml and the snow CLI — skipping install"
  exit 0
fi

if command -v cortex >/dev/null 2>&1; then
  ok "Cortex already installed: $(cortex --version 2>/dev/null || true)"
  exit 0
fi

if [[ "$COMMON_DRY_RUN" == "1" ]]; then
  info "[DRY RUN] Would install Cortex Code CLI"
  exit 0
fi

case "$(uname -s)" in
Darwin | Linux) curl -LsS https://ai.snowflake.com/static/cc-scripts/install.sh | sh ;;
*)
  if command -v pwsh >/dev/null 2>&1; then
    pwsh -NoProfile -Command 'irm https://ai.snowflake.com/static/cc-scripts/install.ps1 | iex'
  else
    die "Cortex installation requires macOS/Linux or PowerShell"
  fi
  ;;
esac

command -v cortex >/dev/null 2>&1 || die "Cortex installation completed but cortex is not on PATH"
ok "Cortex installed: $(cortex --version 2>/dev/null || true)"
