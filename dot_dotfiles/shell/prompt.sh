# shellcheck shell=bash
# ~/.dotfiles/shell/prompt.sh
# Shell prompt configuration.
# Sourced by both .bashrc and .zshrc (via .bashrc).
# Detects the current shell and uses the correct starship init command.
# When starship is absent, falls back to a shell-appropriate prompt.

if command -v starship &>/dev/null; then
  if [[ -n "$ZSH_VERSION" ]]; then
    eval "$(starship init zsh)"
  else
    eval "$(starship init bash)"
  fi
  return 0
fi

# Fallback prompt when starship is not installed
if [[ -n "$ZSH_VERSION" ]]; then
  # zsh: use %F/%f color syntax (zsh-native)
  # shellcheck disable=SC2034
  PROMPT='%F{green}%n@%m%f:%F{blue}%~%f%f%% '
else
  # bash: use \[\] color escapes (bash-native)
  [ -z "$debian_chroot" ] && [ -r /etc/debian_chroot ] && debian_chroot=$(cat /etc/debian_chroot)

  if [ -x "$(which xterm-color)" ]; then
    case "$TERM" in
    xterm-color | *-256color) color_prompt=yes ;;
    esac
  fi
  if [ -n "$debian_chroot" ]; then
    if [ -n "$force_color_prompt" ]; then
      if [ -x "$(which tput)" ] && tput setaf 1 >&/dev/null; then
        color_prompt=yes
      else
        color_prompt=
      fi
    fi
    if [ "$color_prompt" = yes ]; then
      PS1='${debian_chroot:+($debian_chroot)}\[\033[01;32m\]\u@\h\[\033[00m\]:\[\033[01;34m\]\w\[\033[00m\]\$ '
    else
      PS1='${debian_chroot:+($debian_chroot)}\u@\h:\w\$ '
    fi
    case "$TERM" in
    xterm* | rxvt*)
      PS1="\[\e]0;${debian_chroot:+($debian_chroot)}\u@\h: \w\a\]$PS1"
      ;;
    esac
    unset color_prompt force_color_prompt
  fi

  if [ -x "$(which dircolors)" ]; then
    test -r $HOME/.dircolors && eval "$(dircolors -b $HOME/.dircolors)" || eval "$(dircolors -b)"
  fi
fi
