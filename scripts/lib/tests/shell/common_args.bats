#!/usr/bin/env bats

setup() {
  export REPO_ROOT="${BATS_TEST_DIRNAME%/scripts/lib/tests/shell}"
  export NO_COLOR=1
}

@test "common args forwards dry-run and leaves operands" {
  run bash -c '
    set -euo pipefail
    source "$1/scripts/lib/common_args.sh"
    COMMON_STRICT=1
    parse_common_args --dry-run operand
    printf "%s|%s|%s\n" "$COMMON_DRY_RUN" "${COMMON_FORWARD_ARGS[*]}" "${COMMON_ARGS_REMAINING[*]}"
  ' bash "$REPO_ROOT"

  [ "$status" -eq 0 ]
  [[ "$output" == *"1|--dry-run|operand"* ]]
}

@test "common args rejects unsupported options in strict mode" {
  run bash -c '
    set -euo pipefail
    source "$1/scripts/lib/common_args.sh"
    COMMON_STRICT=1
    parse_common_args --not-supported
  ' bash "$REPO_ROOT"

  [ "$status" -eq 2 ]
  [[ "$output" == *"Unknown option: --not-supported"* ]]
}
