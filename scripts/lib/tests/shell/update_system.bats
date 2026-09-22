#!/usr/bin/env bats

setup() {
  export REPO_ROOT="${BATS_TEST_DIRNAME%/scripts/lib/tests/shell}"
  export NO_COLOR=1
  export HOME="$BATS_TEST_TMPDIR/home"
  mkdir -p "$HOME"
}

@test "update-system skips when its gate is disabled" {
  run env -u DOTFILES_RUN_UPDATE_SYSTEM_SETUP \
    bash "$REPO_ROOT/scripts/update-system.sh"

  [ "$status" -eq 0 ]
  [[ "$output" == *"skipping system update"* ]]
}

@test "update-system dry run is side-effect free" {
  run env DOTFILES_RUN_UPDATE_SYSTEM_SETUP=1 \
    bash "$REPO_ROOT/scripts/update-system.sh" --dry-run

  [ "$status" -eq 0 ]
  [[ "$output" == *"[DRY RUN] Would update"* ]]
}
