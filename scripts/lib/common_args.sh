#!/usr/bin/env bash

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
      printf '%s\n' "Usage: ${COMMON_USAGE:-$0 [options]}"
      [[ -z "${COMMON_HELP_TEXT:-}" ]] || printf '%s\n' "$COMMON_HELP_TEXT"
      exit 0
      ;;
    --dry-run)
      COMMON_DRY_RUN=1
      COMMON_FORWARD_ARGS+=("--dry-run")
      ;;
    --no-backup)
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
