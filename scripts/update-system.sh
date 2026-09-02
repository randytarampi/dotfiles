#!/usr/bin/env bash
set -euo pipefail

_SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
LIB_DIR="$SCRIPT_DIR/lib"

# shellcheck disable=SC1091
source "$LIB_DIR/common.sh"
# shellcheck disable=SC1091
source "$LIB_DIR/common_args.sh"
export COMMON_USAGE="$0"
export COMMON_HELP_TEXT="Update system packages and toolchains across all packagers."
export COMMON_STRICT=1
parse_common_args "$@"
set -- ${COMMON_ARGS_REMAINING[@]+"${COMMON_ARGS_REMAINING[@]}"}
if (($# > 0)); then
  printf 'Error: unexpected operand: %s\n' "$1" >&2
  exit 2
fi
# shellcheck disable=SC1091
source "$LIB_DIR/env.sh"

load_env || warn "\$HOME/.env not found, skipping env load"

if [[ "${DOTFILES_RUN_UPDATE_SYSTEM_SETUP:-0}" != "1" ]]; then
  info "DOTFILES_RUN_UPDATE_SYSTEM_SETUP='${DOTFILES_RUN_UPDATE_SYSTEM_SETUP:-0}' — skipping system update"
  exit 0
fi

if [[ "$COMMON_DRY_RUN" == "1" ]]; then
  info "[DRY RUN] Would update: brew (macOS), apt/dnf/pacman (Linux), winget (Windows), npm globals, uv tools, pipx tools, pi, opencode, junie, ollama models"
  exit 0
fi

failures=0
warnings=0
ran_lanes=()
skipped_lanes=()
failed_lanes=()
warned_lanes=()

run_update() {
  local lane="$1"
  shift
  if "$@"; then
    ran_lanes+=("$lane")
  else
    failures=$((failures + 1))
    failed_lanes+=("$lane")
    warn "$lane update failed"
  fi
}

# shellcheck disable=SC2329
pull_ollama_models() {
  local failed=0 model
  while IFS= read -r model; do
    [[ -z "$model" ]] && continue
    if ! ollama pull "$model"; then
      warn "ollama pull failed: $model"
      failed=1
    fi
  done < <(ollama list 2>/dev/null | awk 'NR>1 && !/reviewer/ {print $1}')
  return "$failed"
}

platform="$(uname -s)"
is_windows=0
case "$platform" in
MINGW* | MSYS* | CYGWIN*) is_windows=1 ;;
esac

if [[ "$platform" == "Darwin" ]] && command -v brew >/dev/null 2>&1; then
  run_update "brew update" brew update --yes
  run_update "brew upgrade" brew upgrade
  run_update "brew cleanup" brew cleanup
  if brew doctor >/dev/null 2>&1; then
    info "brew doctor passed"
  else
    warn "brew doctor reported issues"
    warnings=$((warnings + 1))
    warned_lanes+=("brew doctor")
  fi
  if command -v python3 >/dev/null 2>&1; then
    run_update "Brewfile sync" python3 "$SCRIPT_DIR/sync-brewfiles.py"
  else
    warn "python3 not found — skipping Brewfile sync"
    skipped_lanes+=("Brewfile sync")
  fi
else
  skipped_lanes+=("brew (macOS)")
fi

if [[ "$platform" == "Linux" && "$is_windows" == "0" ]]; then
  if command -v apt-get >/dev/null 2>&1; then
    run_update "apt" bash -c 'sudo apt-get update && sudo apt-get upgrade -y'
  elif command -v dnf >/dev/null 2>&1; then
    run_update "dnf" sudo dnf upgrade -y
  elif command -v pacman >/dev/null 2>&1; then
    run_update "pacman" sudo pacman -Syu --noconfirm
  else
    info "No known Linux package manager found — skipping system packages"
    skipped_lanes+=("Linux system packages")
  fi
else
  skipped_lanes+=("Linux system packages")
fi

if [[ "$is_windows" == "1" ]] && command -v winget >/dev/null 2>&1; then
  run_update "winget" winget upgrade --all --include-unknown --silent --accept-package-agreements --accept-source-agreements
else
  skipped_lanes+=("winget")
fi

if command -v npm >/dev/null 2>&1; then
  if [[ "$is_windows" == "1" ]]; then
    run_update "npm globals" npm update -g
  else
    run_update "npm globals" bash "$SCRIPT_DIR/update-nvm-globals.sh"
  fi
else
  skipped_lanes+=("npm globals")
fi

if command -v uv >/dev/null 2>&1; then
  run_update "uv tools" uv tool upgrade --all
else
  skipped_lanes+=("uv tools")
fi

if command -v pipx >/dev/null 2>&1; then
  run_update "pipx tools" pipx upgrade-all
else
  skipped_lanes+=("pipx tools")
fi

if command -v pi >/dev/null 2>&1; then
  run_update "pi" pi update --all
else
  skipped_lanes+=("pi")
fi

if command -v opencode >/dev/null 2>&1; then
  run_update "opencode" opencode upgrade
else
  skipped_lanes+=("opencode")
fi

if command -v junie >/dev/null 2>&1; then
  run_update "junie" junie update
else
  info "junie not found — skipping"
  skipped_lanes+=("junie")
fi

if command -v ollama >/dev/null 2>&1; then
  if ollama list >/dev/null 2>&1; then
    run_update "ollama models" pull_ollama_models
  else
    warn "ollama daemon unreachable — skipping model pulls"
    skipped_lanes+=("ollama models")
  fi
else
  skipped_lanes+=("ollama models")
fi

summary="System update complete.\n\nRan: ${ran_lanes[*]:-none}\nSkipped: ${skipped_lanes[*]:-none}\nWarned (${warnings}): ${warned_lanes[*]:-none}\nFailed (${failures}): ${failed_lanes[*]:-none}"
if [[ "$failures" -gt 0 ]]; then
  warn "$summary"
else
  ok "$summary"
fi
if [[ "$failures" -gt 0 ]]; then
  exit 1
fi
exit 0
