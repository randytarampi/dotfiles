# shellcheck shell=bash
alias ll="ls -larth"
alias rm="rm -v"
alias mv="mv -vi"
alias cp="cp -vi"
alias du="du -sm"
alias ps="ps -f"

# Dynamic neofetch: prefers neowofetch > fastfetch > neofetch
if command -v neowofetch >/dev/null 2>&1; then
  alias neofetch='neowofetch'
elif command -v fastfetch >/dev/null 2>&1; then
  alias neofetch='fastfetch'
fi

# OpenCode with automatic multiplexer support
# Inside tmux/zellij: injects --port for the TUI (default command) only.
# Uses a random high port to avoid conflicts with other opencode instances (ACP, serve, etc.).
# Subcommands (models, serve, run, etc.) pass through without --port.
# Outside multiplexer: passes through to opencode unchanged.
opencode() {
  _opencode_subcommands="completion acp mcp attach run debug providers auth agent upgrade uninstall serve web models stats export import github pr session plugin db"
  if { [ -n "${TMUX:-}" ] || [ -n "${ZELLIJ:-}" ]; } && ! echo "$_opencode_subcommands" | grep -qw -- "${1:-__default__}"; then
    export OPENCODE_PORT="${OPENCODE_PORT:-$(jot -r 1 49152 65535)}"
    command opencode --port "$OPENCODE_PORT" "$@"
  else
    command opencode "$@"
  fi
}

# SmallCode passthrough wrapper.
smallcode() {
  command smallcode "$@"
}
