#!/usr/bin/env bats

setup() {
  export REPO_ROOT="${BATS_TEST_DIRNAME%/scripts/lib/tests/shell}"
  export NO_COLOR=1
  export HOME="$BATS_TEST_TMPDIR/home"
  export STUB_BIN="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$HOME" "$STUB_BIN"
  printf '#!/usr/bin/env bash\nprintf "v20.0.0\\n"\n' > "$STUB_BIN/node"
  chmod +x "$STUB_BIN/node"
}

@test "npm reconciliation dry run lists packages without invoking npm" {
  printf '# npm packages\nnpm "alpha"\nnpm "corepack"\n' > "$BATS_TEST_TMPDIR/Brewfile"
  printf '#!/usr/bin/env bash\nprintf "npm-called\\n" >&2\nexit 99\n' > "$STUB_BIN/npm"
  chmod +x "$STUB_BIN/npm"

  run env PATH="$STUB_BIN:$PATH" bash \
    "$REPO_ROOT/scripts/install-npm-brewfile-packages.sh" \
    "$BATS_TEST_TMPDIR/Brewfile" --dry-run

  [ "$status" -eq 0 ]
  [[ "$output" == *"Would install alpha@latest globally"* ]]
  [[ "$output" == *"Would run corepack enable"* ]]
  [[ "$output" != *"npm-called"* ]]
}

@test "npm reconciliation counts successful and failed installs" {
  printf 'npm "alpha"\nnpm "broken"\n' > "$BATS_TEST_TMPDIR/Brewfile"
  printf '#!/usr/bin/env bash\nif [[ "$3" == "broken@latest" ]]; then exit 1; fi\nexit 0\n' > "$STUB_BIN/npm"
  chmod +x "$STUB_BIN/npm"

  run env PATH="$STUB_BIN:$PATH" bash \
    "$REPO_ROOT/scripts/install-npm-brewfile-packages.sh" \
    "$BATS_TEST_TMPDIR/Brewfile"

  [ "$status" -eq 1 ]
  [[ "$output" == *"Installed/updated: 1"* ]]
  [[ "$output" == *"Failed: 1"* ]]
}
