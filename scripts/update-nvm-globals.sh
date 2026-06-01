#!/usr/bin/env bash
set -euo pipefail

# update-nvm-globals.sh — Update npm global packages across all installed node versions
#
# 1. Updates globals on the default version (npm update -g)
# 2. Reinstalls packages from default into every other installed nvm version
# 3. Updates globals on the system (Homebrew) node
# 4. Returns to the default version

_SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"

source "$SCRIPT_DIR/lib/common.sh"

export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"

if ! command -v nvm >/dev/null 2>&1; then
  die "nvm not found. Ensure NVM_DIR=$NVM_DIR and nvm.sh is sourced."
fi

DEFAULT_VERSION=$(nvm version default 2>/dev/null)
if [[ -z "$DEFAULT_VERSION" || "$DEFAULT_VERSION" == "N/A" ]]; then
  die "No nvm default version set. Run: nvm alias default <version>"
fi

info "Default node version: $DEFAULT_VERSION"

# Step 1: Update global packages on the default version
info "Updating global packages on $DEFAULT_VERSION..."
nvm use default >/dev/null 2>&1
npm update -g 2>&1 || warn "npm update -g on $DEFAULT_VERSION had failures"

# Step 2: Get all other installed versions (sorted newest-first, deduplicated, excluding default)
OTHER_VERSIONS=$(nvm ls --no-colors 2>/dev/null |
  grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+' |
  grep -v "^$DEFAULT_VERSION$" |
  sort -rV |
  uniq)

if [[ -z "$OTHER_VERSIONS" ]]; then
  ok "Only one installed version ($DEFAULT_VERSION). Nothing to propagate."
  exit 0
fi

info "Propagating packages from $DEFAULT_VERSION to other versions..."

for ver in $OTHER_VERSIONS; do
  info "Reinstalling packages on $ver from $DEFAULT_VERSION..."
  nvm use "$ver" >/dev/null 2>&1
  nvm reinstall-packages default 2>&1 || warn "reinstall-packages on $ver had failures"
done

# Step 3: Update global packages on the system (Homebrew) node
SYSTEM_NPM=""
if command -v brew >/dev/null 2>&1; then
  _brew_prefix="$(brew --prefix 2>/dev/null)"
  if [[ -x "$_brew_prefix/bin/npm" ]]; then
    SYSTEM_NPM="$_brew_prefix/bin/npm"
  fi
fi
# Also try standard Homebrew paths if brew isn't in PATH (unlikely but defensive)
if [[ -z "$SYSTEM_NPM" ]]; then
  for _prefix in /opt/homebrew /usr/local; do
    if [[ -x "$_prefix/bin/npm" ]]; then
      SYSTEM_NPM="$_prefix/bin/npm"
      break
    fi
  done
fi

if [[ -n "$SYSTEM_NPM" ]]; then
  _system_node_ver="$("${SYSTEM_NPM%/*}/node" --version 2>/dev/null || echo "unknown")"
  info "Updating global packages on system node ($_system_node_ver)..."
  # Use full path to avoid nvm intercepting the npm call
  "$SYSTEM_NPM" update -g 2>&1 || warn "npm update -g on system node had failures"
else
  info "No system (Homebrew) node found — skipping"
fi

# Step 4: Return to default
nvm use default >/dev/null 2>&1

ok "Global packages updated across all node versions.

Default: $DEFAULT_VERSION
nvm versions: $(echo "$OTHER_VERSIONS" | tr '\n' ', ' | sed 's/,$//')
$(if [[ -n "$SYSTEM_NPM" ]]; then echo "System node: $_system_node_ver"; else echo "System node: none found"; fi)

Update script complete!"
