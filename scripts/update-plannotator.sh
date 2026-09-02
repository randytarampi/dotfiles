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
export COMMON_HELP_TEXT="Update Plannotator CLI only when the installed version is older than the latest release."
export COMMON_STRICT=1
parse_common_args "$@"

if [[ "${DOTFILES_RUN_PLANNOTATOR_SETUP:-0}" != "1" ]]; then
  info "DOTFILES_RUN_PLANNOTATOR_SETUP='${DOTFILES_RUN_PLANNOTATOR_SETUP:-0}' — skipping plannotator update"
  exit 0
fi

if [[ "$COMMON_DRY_RUN" == "1" ]]; then
  info "[DRY RUN] Would check for and update Plannotator"
  exit 0
fi

version_ge() {
  local left right i left_prerelease=0 right_prerelease=0
  local -a left_parts right_parts
  left="${1#v}"
  left="${left#V}"
  right="${2#v}"
  right="${right#V}"
  if [[ "$left" == *-* ]]; then
    left_prerelease=1
    left="${left%%-*}"
  fi
  if [[ "$right" == *-* ]]; then
    right_prerelease=1
    right="${right%%-*}"
  fi
  IFS=. read -r -a left_parts <<<"$left"
  IFS=. read -r -a right_parts <<<"$right"
  for ((i = 0; i < 3; i++)); do
    left="${left_parts[i]:-0}"
    right="${right_parts[i]:-0}"
    left="${left%%[^0-9]*}"
    right="${right%%[^0-9]*}"
    left="${left:-0}"
    right="${right:-0}"
    if ((left > right)); then return 0; fi
    if ((left < right)); then return 1; fi
  done
  if [[ "$left_prerelease" == "1" ]]; then return 1; fi
  if [[ "$right_prerelease" == "1" ]]; then return 0; fi
  return 0
}

CURRENT=""
if command -v plannotator >/dev/null 2>&1; then
  CURRENT="$(plannotator --version 2>/dev/null | awk '{print $NF}')"
fi

CURL_ARGS=(-fsSL "https://api.github.com/repos/backnotprop/plannotator/releases/latest")
if [[ -n "${GH_TOKEN:-}" ]]; then
  CURL_ARGS+=(-H "Authorization: Bearer ${GH_TOKEN}")
fi
LATEST_TAG="$(curl "${CURL_ARGS[@]}" 2>/dev/null |
  python3 -c 'import json,sys; print(json.load(sys.stdin).get("tag_name",""))' 2>/dev/null || true)"
if [[ -z "$LATEST_TAG" && -n "${GH_TOKEN:-}" ]]; then
  LATEST_TAG="$(curl -fsSL "https://api.github.com/repos/backnotprop/plannotator/releases/latest" 2>/dev/null |
    python3 -c 'import json,sys; print(json.load(sys.stdin).get("tag_name",""))' 2>/dev/null || true)"
fi
LATEST="${LATEST_TAG#v}"

install_plannotator() {
  info "Installing/updating plannotator..."
  if [[ "$(uname -s)" == "Darwin" ]] || [[ "$(uname -s)" == "Linux" ]]; then
    if ! curl -fsSL https://plannotator.ai/install.sh | bash -s -- --no-extras --model-invocable none 2>&1; then
      warn "plannotator install fetch failed (network error?) — try again with: curl -fsSL https://plannotator.ai/install.sh | bash"
      return 1
    fi
  elif [[ "$(uname -s)" == *"MINGW"* ]] || [[ "$(uname -s)" == *"MSYS"* ]] || [[ "$(uname -s)" == *"CYGWIN"* ]]; then
    if ! powershell -NoProfile -ExecutionPolicy Bypass -Command "iex (irm 'https://plannotator.ai/install.ps1')" 2>&1; then
      warn "plannotator install fetch failed (network error?) — try again manually"
      return 1
    fi
  else
    warn "Unsupported OS for plannotator: $(uname -s)"
    return 1
  fi
}

INSTALL_FAILED=0
if [[ -z "$CURRENT" ]]; then
  install_plannotator || INSTALL_FAILED=1
elif [[ -z "$LATEST_TAG" ]]; then
  warn "Could not fetch latest Plannotator version — skipping update check"
  exit 0
elif [[ "$CURRENT" == "$LATEST" ]] || version_ge "$CURRENT" "$LATEST"; then
  ok "Plannotator ${CURRENT} is up to date — skipping install"
  exit 0
else
  info "Updating Plannotator ${CURRENT} → ${LATEST}"
  install_plannotator || INSTALL_FAILED=1
fi

if [[ "$INSTALL_FAILED" == "1" ]]; then
  if [[ -n "$CURRENT" ]]; then
    warn "Plannotator installation failed — remaining at ${CURRENT}"
  else
    warn "plannotator installation failed — not found on PATH after install"
  fi
  exit 0
fi

if command -v plannotator >/dev/null 2>&1; then
  PLANNOTATOR_VERSION="$(plannotator --version 2>&1 || echo "unknown")"
  ok "plannotator installed/updated: $PLANNOTATOR_VERSION at $(command -v plannotator)"
else
  warn "plannotator install may have failed — not found on PATH after install"
fi
