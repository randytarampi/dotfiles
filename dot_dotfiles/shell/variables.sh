# shellcheck shell=bash
# ~/.dotfiles/shell/variables.sh
# Shell environment variables (non-secret + secret).
# This file is sourced by .bashrc.
# Add any custom environment variables here.

# Load secrets from ~/.env (replaces ~/.credentials.sh)
[ -f "$HOME/.env" ] && set -a && source "$HOME/.env" && set +a

# History configuration (defaults can be overridden from ~/.env)
export HISTCONTROL="${HISTCONTROL:-ignoreboth}"
export HISTSIZE="${HISTSIZE:-1000}"
export HISTFILESIZE="${HISTFILESIZE:-2000}"

# iTerm2 integration
export ITERM_ENABLE_SHELL_INTEGRATION_WITH_TMUX="${ITERM_ENABLE_SHELL_INTEGRATION_WITH_TMUX:-YES}"

# Terminal display
export CLICOLOR="${CLICOLOR:-1}"
export GREP_COLOR="${GREP_COLOR:-1}"
export GCC_COLORS="${GCC_COLORS:-error=01;31:warning=01;35:note=01;36:caret=01;32:locus=01:quote=01}"
