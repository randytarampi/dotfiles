# shellcheck shell=bash
[ -d "$HOME/.bun" ] && export BUN_INSTALL="$HOME/.bun"
[ -n "$BUN_INSTALL" ] && export PATH="$BUN_INSTALL/bin:$PATH"
[ -s "$HOME/.bun/_bun" ] && source "$HOME/.bun/_bun"
