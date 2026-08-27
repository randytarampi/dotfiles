#!/usr/bin/env bash

# Print colored or plain help depending on terminal capability.
# Mirrors the color detection in common.sh: respects NO_COLOR, TERM=dumb,
# and whether stdout is a TTY. Works on macOS, Linux, and Git Bash on Windows.
# Output format mimics Python argparse: lowercase "usage:" in blue,
# command basename in bold magenta, flags in bold cyan, metavars in yellow,
# description block, then "options:" in blue with two-column layout.
_print_help() {
  local use_color=true
  if [[ -n "${NO_COLOR:-}" || "${TERM:-}" == "dumb" || ! -t 1 ]]; then
    use_color=false
  fi

  local raw_usage="${COMMON_USAGE:-$0 [options]}"
  local help_text="${COMMON_HELP_TEXT:-}"

  # Color codes
  local c_reset="" c_usage="" c_cmd="" c_flag="" c_metavar=""
  if [[ "$use_color" == "true" ]]; then
    c_reset=$'\033[0m'
    c_usage=$'\033[1;34m'   # bold blue
    c_cmd=$'\033[1;35m'     # bold magenta
    c_flag=$'\033[1;36m'    # bold cyan
    c_metavar=$'\033[0;33m' # yellow
  fi

  # ── usage: line ──
  # Use basename for command, colorize "usage:" in blue, command in magenta
  local cmd_basename opts_part
  if [[ "$raw_usage" =~ ^([^[:space:]]+)([[:space:]].*)?$ ]]; then
    cmd_basename="$(basename "${BASH_REMATCH[1]}")"
    opts_part="${BASH_REMATCH[2]:-}"
  else
    cmd_basename="$(basename "$raw_usage")"
    opts_part=""
  fi
  if [[ "$use_color" == "true" ]]; then
    local colored_opts="$opts_part"
    # Colorize --flag patterns in bold cyan
    colored_opts=$(printf '%s' "$colored_opts" | sed "s/--[a-zA-Z][a-zA-Z0-9-]*/${c_flag}&${c_reset}/g")
    # Colorize UPPERCASE metavars (STEP, TIER, MODE, N) and mixed-case metavars (C=, R=M) in yellow
    colored_opts=$(printf '%s' "$colored_opts" | sed -E "s/[[:space:]]([A-Z_][A-Z_0-9\]\[,\.\-]*|[A-Z]=[A-Za-z]*)/${c_metavar}\1${c_reset}/g")
    printf '%s\n' "${c_usage}usage:${c_reset} ${c_cmd}${cmd_basename}${c_reset}${colored_opts}"
  else
    printf 'usage: %s%s\n' "$cmd_basename" "$opts_part"
  fi

  # ── description + options block ──
  if [[ -z "$help_text" ]]; then
    return
  fi

  # Split help_text into description lines and flag lines
  local in_options=0
  local desc_lines=()
  local flag_lines=()

  while IFS= read -r line; do
    if [[ "$line" =~ ^[Aa]vailable\ [Ff]lags: ]] || [[ "$line" =~ ^[Oo]ptions: ]]; then
      in_options=1
      continue
    fi
    if [[ "$in_options" == "1" ]]; then
      flag_lines+=("$line")
    else
      desc_lines+=("$line")
    fi
  done <<<"$help_text"

  # Print blank line, then description
  printf '\n'
  if [[ "${#desc_lines[@]}" -gt 0 ]]; then
    local last_nonempty=-1
    for i in "${!desc_lines[@]}"; do
      [[ -n "${desc_lines[$i]}" ]] && last_nonempty=$i
    done
    for ((i = 0; i <= last_nonempty; i++)); do
      printf '%s\n' "${desc_lines[$i]}"
    done
  fi

  # Print blank line, then "options:" header, then flag lines
  if [[ "${#flag_lines[@]}" -gt 0 ]]; then
    printf '\n'
    if [[ "$use_color" == "true" ]]; then
      printf '%soptions:%s\n' "$c_usage" "$c_reset"
    else
      printf 'options:\n'
    fi

    # Calculate max flag column width for alignment
    local max_flag_width=0
    for line in "${flag_lines[@]}"; do
      if [[ "$line" =~ ^([[:space:]]+--[a-zA-Z][a-zA-Z0-9-]*(.*)?)([[:space:]]{2,}) ]]; then
        local flag_w
        flag_w=$(printf '%s' "${BASH_REMATCH[1]}" | awk '{print length}')
        ((flag_w > max_flag_width)) && max_flag_width=$flag_w
      fi
    done

    for line in "${flag_lines[@]}"; do
      if [[ "$use_color" == "true" ]] && [[ "$line" =~ ^([[:space:]]+)(.+)$ ]]; then
        local indent="${BASH_REMATCH[1]}"
        local rest="${BASH_REMATCH[2]}"
        # Split at first 2+ space gap into flag_part and description
        local flag_part="" desc_part=""
        if [[ "$rest" =~ ^([^[:space:]].*[^[:space:]])[[:space:]]{2,}(.+)$ ]]; then
          flag_part="${BASH_REMATCH[1]}"
          desc_part="${BASH_REMATCH[2]}"
        elif [[ "$rest" =~ ^([^[:space:]].*[^[:space:]])[[:space:]]+$ ]]; then
          flag_part="${BASH_REMATCH[1]}"
        else
          flag_part="$rest"
        fi
        # Pad to align descriptions
        local flag_col="${indent}${flag_part}"
        local flag_col_w
        flag_col_w=$(printf '%s' "$flag_col" | awk '{print length}')
        local pad_width=$((max_flag_width - flag_col_w))
        local padding=""
        ((pad_width > 0)) && padding=$(printf '%*s' "$pad_width" '')
        # Colorize: --flag in bold cyan, metavars in yellow
        # Split flag_part at first space: "--flag" and "METAVAR"
        local colored_flag_part
        if [[ "$flag_part" =~ ^(--[a-zA-Z][a-zA-Z0-9-]*)([[:space:]].+)?$ ]]; then
          local fname="${BASH_REMATCH[1]}"
          local fmeta="${BASH_REMATCH[2]:-}"
          if [[ -n "$fmeta" ]]; then
            colored_flag_part="${c_flag}${fname}${c_reset}${c_metavar}${fmeta}${c_reset}"
          else
            colored_flag_part="${c_flag}${fname}${c_reset}"
          fi
        else
          colored_flag_part="$flag_part"
        fi
        printf '  %s%s  %s\n' "$colored_flag_part" "$padding" "$desc_part"
      else
        printf '%s\n' "$line"
      fi
    done
  fi
}

parse_common_args() {
  COMMON_HELP=0
  COMMON_DRY_RUN=0
  COMMON_NO_BACKUP=0
  COMMON_ARGS_REMAINING=()
  COMMON_FORWARD_ARGS=()
  local arg
  for arg in "$@"; do
    case "$arg" in
    --help)
      COMMON_HELP=1
      _print_help
      exit 0
      ;;
    --dry-run)
      COMMON_DRY_RUN=1
      COMMON_FORWARD_ARGS+=("--dry-run")
      ;;
    --no-backup)
      if [[ "${COMMON_ALLOW_NO_BACKUP:-false}" != "true" ]]; then
        printf 'Unknown option: %s\n' "$arg" >&2
        exit 2
      fi
      COMMON_NO_BACKUP=1
      COMMON_FORWARD_ARGS+=("--no-backup")
      ;;
    *)
      if [[ "${COMMON_STRICT:-0}" == "1" && "$arg" == -* ]]; then
        printf 'Unknown option: %s\n' "$arg" >&2
        exit 2
      fi
      COMMON_ARGS_REMAINING+=("$arg")
      ;;
    esac
  done

  # These values are intentionally consumed by the sourcing caller.
  : "$COMMON_HELP" "$COMMON_DRY_RUN" "$COMMON_NO_BACKUP"
}

add_common_args() {
  local no_backup="${1:-false}"
  ADD_ARG="--dry-run"
  COMMON_ALLOW_NO_BACKUP="$no_backup"
  if [ "$no_backup" = "true" ]; then
    ADD_ARG="$ADD_ARG --no-backup"
  fi
}
