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

setup_managed_opencode() {
  # Whole-script stub environment: every lane tool is a recording no-op stub
  # so the real update lane runs offline. The managed opencode lives in
  # $HOME/.opencode/bin (official-installer destination) while a stale v1
  # opencode shadows the front of PATH. python3/bash/chezmoi resolve REAL
  # (later PATH dirs); curl/brew are stubbed to protect network/registry.
  local bin="$BATS_TEST_TMPDIR/stubbin"
  mkdir -p "$bin" "$HOME/.opencode/bin" "$HOME/.config/opencode"
  # Stub nvm.sh so the npm-globals lane succeeds deterministically on any
  # host (a brew-nvm on the owner machine masked this in local runs).
  mkdir -p "$HOME/.nvm"
  cat > "$HOME/.nvm/nvm.sh" <<'NVMSH'
nvm() {
  case "$1 $2" in
    "version default") printf "v22.1.0\n" ;;
    "ls"*) printf "v22.1.0\nv20.11.0\n" ;;
    *) return 0 ;;
  esac
}
NVMSH
  chmod +x "$HOME/.nvm/nvm.sh"
  local tool
  for tool in brew apt-get dnf pacman winget npm uv pipx pi junie ollama omllx curl; do
    printf '#!/bin/sh\nprintf "%%s|%%s\\n" "$(basename "$0")" "$*" >> "%s"\nexit 0\n' "$BATS_TEST_TMPDIR/calls" > "$bin/$tool"
    chmod +x "$bin/$tool"
  done
  # PATH opencode: stale v1 shadow that records any invocation.
  printf '#!/bin/sh\nprintf "opencode 1.19.5\\n"\nprintf "%%s PATHV1 %%s\\n" "$(basename "$0")" "$*" >> "%s"\nexit 0\n' "$BATS_TEST_TMPDIR/calls" > "$bin/opencode"
  chmod +x "$bin/opencode"
  # Managed v2 binary: records, answers probes and plugin checks.
  printf '#!/bin/sh\nprintf "%%s MANAGED %%s\\n" "$(basename "$0")" "$*" >> "%s"\nif [ "$1" = --version ]; then printf "opencode v2.2.5\\n"; exit 0; fi\nif [ "$1" = plugin ] && [ "$2" = check ]; then n="$3"; n="${n%%@latest}"; n="${n%%#*}"; n="${n##*/}"; printf "%%s 2.2.5 (current)\\n" "$n"; exit 0; fi\ncat >/dev/null\nexit 0\n' "$BATS_TEST_TMPDIR/calls" > "$HOME/.opencode/bin/opencode"
  chmod +x "$HOME/.opencode/bin/opencode"
  : > "$BATS_TEST_TMPDIR/calls"
}

@test "opencode upgrade lane uses the managed binary, never the PATH v1" {
  setup_managed_opencode
  run env DOTFILES_RUN_UPDATE_SYSTEM_SETUP=1 DOTFILES_RUN_OPENCODE_TOOLS_SETUP=1 \
    PATH="$BATS_TEST_TMPDIR/stubbin:$HOME/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" \
    bash "$REPO_ROOT/scripts/update-system.sh"
  [ "$status" -eq 0 ]
  calls="$(cat "$BATS_TEST_TMPDIR/calls")"
  [[ "$calls" == *"MANAGED upgrade"* ]]
  [[ "$calls" == *"MANAGED --version"* ]]
  ! grep -q "PATHV1 upgrade" "$BATS_TEST_TMPDIR/calls"
  # Install-time adds ran through the managed binary (update gate still off:
  # adds + version probes only, no update rechecks by design).
  [[ "$calls" == *"MANAGED plugin add"* ]]
  ! grep -q "MANAGED plugin update" "$BATS_TEST_TMPDIR/calls"
  [[ "$output" == *"Failed (0): none"* ]]

  # Same environment with the plugin-update gate on: adds + per-spec checks
  # + updates + convergence rechecks, all managed, still no PATH v1.
  : > "$BATS_TEST_TMPDIR/calls"
  run env DOTFILES_RUN_UPDATE_SYSTEM_SETUP=1 DOTFILES_RUN_OPENCODE_TOOLS_SETUP=1 \
    DOTFILES_RUN_OPENCODE_PLUGIN_UPDATE=1 \
    PATH="$BATS_TEST_TMPDIR/stubbin:$HOME/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" \
    bash "$REPO_ROOT/scripts/update-system.sh"
  [ "$status" -eq 0 ]
  calls="$(cat "$BATS_TEST_TMPDIR/calls")"
  [[ "$calls" == *"MANAGED plugin check"* ]]
  [[ "$calls" == *"MANAGED plugin update"* ]]
  ! grep -q "PATHV1 plugin" "$BATS_TEST_TMPDIR/calls"
}

@test "opencode lane fails closed when the managed install cannot be proved" {
  setup_managed_opencode
  rm -f "$HOME/.opencode/bin/opencode"
  rm -f "$BATS_TEST_TMPDIR/stubbin/opencode"
  # No PATH binary at all + broken channel stub (curl emits nothing) ->
  # the installer must fail closed and the upgrade must never run.
  run env DOTFILES_RUN_UPDATE_SYSTEM_SETUP=1 DOTFILES_RUN_OPENCODE_TOOLS_SETUP=1 \
    PATH="$BATS_TEST_TMPDIR/stubbin:$HOME/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" \
    bash "$REPO_ROOT/scripts/update-system.sh"

  [ "$status" -eq 1 ]
  [[ "$output" == *"opencode install/verify"* ]]
  ! grep -q "PATHV1 upgrade" "$BATS_TEST_TMPDIR/calls" || true
  ! grep -q "MANAGED upgrade" "$BATS_TEST_TMPDIR/calls"
}

@test "opencode lane records a failing managed upgrade as a failure" {
  setup_managed_opencode
  printf '#!/bin/sh\nif [ "$1" = --version ]; then printf "opencode v2.2.5\\n"; exit 0; fi\nexit 1\n' > "$HOME/.opencode/bin/opencode"
  chmod +x "$HOME/.opencode/bin/opencode"
  run env DOTFILES_RUN_UPDATE_SYSTEM_SETUP=1 DOTFILES_RUN_OPENCODE_TOOLS_SETUP=1 \
    PATH="$BATS_TEST_TMPDIR/stubbin:$HOME/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" \
    bash "$REPO_ROOT/scripts/update-system.sh"

  [ "$status" -eq 1 ]
  [[ "$output" == *"opencode upgrade"* ]]
  ! grep -q "PATHV1 upgrade" "$BATS_TEST_TMPDIR/calls"
}
