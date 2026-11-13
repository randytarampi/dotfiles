#!/usr/bin/env bash
# Render the chezmoi .sh interpreter used by Windows/Git Bash deployments.
# Preview is read-only; writing requires an explicit destination.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: windows-chezmoi-interpreter.sh [--print | --check FILE | --write FILE [--force]]

Run from Git Bash on Windows. The default --print renders TOML to stdout.
--check compares an existing config without modifying it.
--write creates FILE atomically; --force explicitly replaces a regular file.
The caller must pass the resulting native config path to chezmoi --config.
EOF
}

die() {
  printf 'Windows chezmoi interpreter: %s\n' "$*" >&2
  exit 1
}

mode=print
output=""
force=0
while [[ $# -gt 0 ]]; do
  case "$1" in
  --print)
    [[ "$mode" == print ]] || die "choose one action"
    shift
    ;;
  --check | --write)
    [[ "$mode" == print && $# -ge 2 && -n "$2" ]] || die "one action and FILE are required"
    mode="${1#--}"
    output="$2"
    shift 2
    ;;
  --force)
    force=1
    shift
    ;;
  -h | --help)
    usage
    exit 0
    ;;
  *)
    die "unknown argument: $1"
    ;;
  esac
done
[[ "$force" == 0 || "$mode" == write ]] || die "--force applies to --write only"

# cygpath is supplied by Git for Windows, but not ordinary Linux or WSL.
# Windows cannot fork/exec a POSIX .sh without this explicit native command.
command -v cygpath >/dev/null 2>&1 || die "Git Bash cygpath is required; WSL is a separate deployment"
bash_path="$(command -v bash)" || die "bash executable missing"
[[ -x "$bash_path" ]] || die "bash is not executable"
native_bash="$(cygpath -w "$bash_path")" || die "cannot translate bash path"
# A TOML literal string preserves backslashes without escaping them. Never
# interpolate a quote or newline into the command value.
[[ "$native_bash" == [A-Za-z]:\\* && "$native_bash" == *.[eE][xX][eE] && "$native_bash" != *"'"* &&
  "$native_bash" != *$'\r'* && "$native_bash" != *$'\n'* ]] ||
  die "invalid native bash.exe path from cygpath"

config="$(printf "[interpreters.sh]\n    command = '%s'\n" "$native_bash")"
case "$mode" in
print)
  printf '%s\n' "$config"
  ;;
check)
  [[ -f "$output" && ! -L "$output" ]] || die "missing or symlinked config: $output"
  [[ "$(cat "$output")" == "$config" ]] || die "interpreter config differs: $output"
  ;;
write)
  parent="$(dirname "$output")"
  [[ -d "$parent" ]] || die "destination directory must already exist: $parent"
  [[ ! -L "$output" ]] || die "refusing symlink destination: $output"
  if [[ -e "$output" ]]; then
    [[ -f "$output" ]] || die "refusing non-regular destination: $output"
    [[ "$force" == 1 ]] || die "refusing to overwrite existing config (use --force)"
  fi
  umask 077
  tmp="$(mktemp "$parent/.chezmoi-interpreter.XXXXXXXX")" || die "cannot create temporary file"
  trap 'rm -f -- "$tmp"' EXIT
  printf '%s\n' "$config" >"$tmp"
  chmod 600 "$tmp"
  # BSD mv on macOS has no -T/--no-target-directory option. Guard the
  # destination type again just before publication to avoid directory-target
  # behaviour without breaking the portable helper tests.
  [[ ! -d "$output" && ! -L "$output" ]] || die "destination changed to directory or symlink: $output"
  mv -f -- "$tmp" "$output"
  trap - EXIT
  ;;
esac
