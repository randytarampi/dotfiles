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
# OpenCode v1 TUIs inside tmux/zellij needed --port for editor-pane
# communication; v2 removed the --port flag and instances share the
# background service, so the port injection applies only while a v1 binary
# resolves first on PATH. Subcommands (models, serve, run, etc.) pass
# through without --port. Outside multiplexer: passes through unchanged.
opencode() {
  _opencode_subcommands="completion acp mcp attach run debug providers auth agent upgrade uninstall serve web models stats export import github pr session plugin db"
  if { [ -n "${TMUX:-}" ] || [ -n "${ZELLIJ:-}" ]; } && ! echo "$_opencode_subcommands" | grep -qw -- "${1:-__default__}"; then
    if [ -z "${_OPENCODE_MAJOR:-}" ]; then
      _opencode_version_banner="$(command opencode --version 2>/dev/null || true)"
      case "${_opencode_version_banner}" in
        *v1.*) _OPENCODE_MAJOR=1 ;;
        *) _OPENCODE_MAJOR=2 ;;
      esac
      export _OPENCODE_MAJOR
    fi
    if [ "${_OPENCODE_MAJOR}" = "1" ]; then
      export OPENCODE_PORT="${OPENCODE_PORT:-$(jot -r 1 49152 65535)}"
      command opencode --port "$OPENCODE_PORT" "$@"
    else
      command opencode "$@"
    fi
  else
    command opencode "$@"
  fi
}
