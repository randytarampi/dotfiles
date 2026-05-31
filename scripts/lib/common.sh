#!/usr/bin/env bash

# Error trap function to print stack trace on command failures

_err_handler() {
  local exit_code="$?"
  if [[ "$exit_code" -ne 0 ]]; then
    local line_no="${1:-$1}"
    local command="${2:-$BASH_COMMAND}"
    local caller_file="unknown"
    if [[ -n "${BASH_SOURCE[1]}" ]]; then
      caller_file=$(basename "${BASH_SOURCE[1]}")
    fi
    local timestamp
    timestamp=$(date +"%Y-%m-%d %H:%M:%S")
    local level_str="[ERROR]"
    local use_color=true
    if [[ -n "${NO_COLOR:-}" || "${TERM:-}" == "dumb" || ! -t 2 ]]; then
      use_color=false
    fi
    if [[ "$use_color" == "true" ]]; then
      level_str="\033[0;31m${level_str}\033[0m"
    fi

    echo -e "${timestamp} ${level_str} (${caller_file}:${line_no}): Command '${command}' failed with exit code ${exit_code}" >&2

    # Print stack trace if nested
    local n=${#BASH_SOURCE[@]}
    if [[ $n -gt 1 ]]; then
      echo -e "${timestamp} ${level_str} (${caller_file}:${line_no}): Shell stack trace:" >&2
      for ((i = 1; i < n; i++)); do
        local file="${BASH_SOURCE[i]}"
        local line="${BASH_LINENO[i - 1]}"
        local func="${FUNCNAME[i]}"
        echo -e "  at ${func:-main} (${file}:${line})" >&2
      done
    fi
  fi
}

if [[ -n "${BASH_VERSION:-}" ]]; then
  trap '_err_handler "$LINENO" "$BASH_COMMAND"' ERR
fi

# Standardized shell log formatting helper

_log() {
  local level="$1"
  local color="$2"
  local msg="$3"
  local stream="${4:-stdout}"

  local use_color=true
  if [[ -n "${NO_COLOR:-}" || "${TERM:-}" == "dumb" ]]; then
    use_color=false
  elif [[ "$stream" == "stdout" && ! -t 1 ]]; then
    use_color=false
  elif [[ "$stream" == "stderr" && ! -t 2 ]]; then
    use_color=false
  fi

  local level_str="[$level]"
  if [[ "$use_color" == "true" ]]; then
    level_str="${color}${level_str}\033[0m"
  fi

  local caller_file="unknown"
  local caller_line="0"
  if [[ -n "${BASH_SOURCE[2]}" ]]; then
    caller_file=$(basename "${BASH_SOURCE[2]}")
    caller_line="${BASH_LINENO[1]:-0}"
  elif [[ -n "${BASH_SOURCE[1]}" ]]; then
    caller_file=$(basename "${BASH_SOURCE[1]}")
    caller_line="${BASH_LINENO[0]:-0}"
  fi

  local timestamp
  timestamp=$(date +"%Y-%m-%d %H:%M:%S")

  local formatted_msg="${timestamp} ${level_str} (${caller_file}:${caller_line}): ${msg}"

  if [[ "$stream" == "stderr" ]]; then
    echo -e "$formatted_msg" >&2
  else
    echo -e "$formatted_msg"
  fi
}

info() {
  _log "INFO" "\033[0;34m" "$1" "stdout"
}

ok() {
  _log "INFO" "\033[0;34m" "$1" "stdout"
}

warn() {
  _log "WARNING" "\033[0;33m" "$1" "stderr"
}

err() {
  _log "ERROR" "\033[0;31m" "$1" "stderr"
}

die() {
  _log "CRITICAL" "\033[1;31m" "$1" "stderr"
  exit 1
}

# run_or_skip <command> <success_msg> <skip_msg>
run_or_skip() {
  local cmd="$1"
  local success_msg="$2"
  local skip_msg="$3"

  if command -v "${cmd%% *}" >/dev/null 2>&1; then
    if eval "$cmd"; then
      ok "$success_msg"
      return 0
    else
      warn "Command failed: $cmd"
      return 1
    fi
  else
    warn "$skip_msg"
    return 0
  fi
}
