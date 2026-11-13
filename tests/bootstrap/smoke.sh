#!/usr/bin/env bash
# Native Linux/container L0 proof, not an installed-package or service test.
set -euo pipefail

if [[ "$(id -u)" -eq 0 ]]; then
  echo "L0 bootstrap smoke must run as a non-root user" >&2
  exit 1
fi

source_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
smoke_home="$(mktemp -d "${TMPDIR:-/tmp}/dotfiles-bootstrap.XXXXXXXX")"
trap 'rm -rf -- "$smoke_home"' EXIT
guard_bin="$smoke_home/deny-bin"
mkdir -p "$guard_bin" "$smoke_home/.config" "$smoke_home/.local/share"
cp "$source_dir/dot_dotfiles/shell/.env.example" "$smoke_home/.env"

# Fail even if an accidentally invoked caller swallows an installer error.
# The trace captures only executable names, never secret-bearing arguments.
for name in curl wget brew winget sudo apt apt-get systemctl launchctl pkill; do
  cat > "$guard_bin/$name" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "${0##*/}" >> "$BOOTSTRAP_SMOKE_TRACE"
exit 96
EOF
  chmod +x "$guard_bin/$name"
done

if grep -Eq "^[[:space:]]*DOTFILES_RUN_[A-Z_]+='1'" "$smoke_home/.env"; then
  echo "Bootstrap fixture unexpectedly enables mutating gates" >&2
  exit 1
fi

# Write the inner shell into the disposable home. This avoids incorrectly
# expanding the *real* operator's HOME, and makes the tested commands reviewable.
cat > "$smoke_home/verify-core.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
# Explicit negative check: the parent's synthetic credential canary may not
# cross the env -i boundary into the deployed process.
if [[ -n "${BOOTSTRAP_SMOKE_CANARY:-}" ]]; then
  echo "Core bootstrap inherited a forbidden parent secret canary" >&2
  exit 1
fi
make deploy
make deploy
chezmoi --source "$SOURCE_DIR" verify
# Do not mask non-zero exit from a substitution inside [[ -n ... ]].
if ! drift="$(chezmoi --source "$SOURCE_DIR" diff)"; then
  echo "chezmoi diff failed; no drift result is available" >&2
  exit 1
fi
if [[ -n "$drift" ]]; then
  echo "chezmoi drift after second bootstrap pass" >&2
  exit 1
fi
test -s "$HOME/.gitconfig"
test -s "$HOME/.bashrc"
test ! -e "$HOME/.local/share/litellm/service.env"
EOF

# This synthetic-only variable proves that env -i strips ambient secrets;
# never use actual provider credentials as test canaries.
export BOOTSTRAP_SMOKE_CANARY=synthetic-parent-only
# No caller HOME, provider keys or host XDG paths inherited by the deployment.
(
  cd "$source_dir"
  env -i \
    HOME="$smoke_home" USER=dotfiles-smoke CI=1 SHELL=/bin/bash \
    PATH="$guard_bin:$PATH" \
    XDG_CONFIG_HOME="$smoke_home/.config" \
    XDG_DATA_HOME="$smoke_home/.local/share" \
    XDG_CACHE_HOME="$smoke_home/.cache" \
    BOOTSTRAP_SMOKE_TRACE="$smoke_home/forbidden.log" \
    SOURCE_DIR="$source_dir" \
    bash "$smoke_home/verify-core.sh"
)
if [[ -s "$smoke_home/forbidden.log" ]]; then
  echo "Core bootstrap invoked forbidden package/network/service tools:" >&2
  cat "$smoke_home/forbidden.log" >&2
  exit 1
fi
echo "L0 core deployed twice without drift or forbidden tools"
