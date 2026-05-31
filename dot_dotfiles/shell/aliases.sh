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
