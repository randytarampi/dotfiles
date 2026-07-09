# shellcheck shell=bash
export PATH=$HOME/.dotfiles/bin:$PATH

# dotfiles scripts (symlinked from ~/.dotfiles/scripts → ~/Development/dotfiles/scripts)
export PATH=$HOME/.dotfiles/scripts:$PATH

# local
export PATH=$HOME/.local/bin:$PATH

# --- macOS-only paths ---
if [[ "$(uname -s)" == "Darwin" ]]; then
  # wireshark
  export PATH="$PATH:/Library/Wireshark"

  # mysql (legacy Oracle installer path)
  export PATH="/usr/local/mysql/bin:$PATH"

  # macports
  export PATH="/opt/local/bin:/opt/local/sbin:$PATH"

  # brew (Intel Mac: /usr/local, ARM Mac: /opt/homebrew)
  # Uses brew --prefix for cross-arch portability, falls back to uname detection
  if command -v brew >/dev/null 2>&1; then
    # shellcheck disable=SC2155
    export HOMEBREW_PREFIX="$(brew --prefix)"
    # shellcheck disable=SC2155
    export HOMEBREW_CELLAR="$(brew --prefix --cellar 2>/dev/null || echo "${HOMEBREW_PREFIX}/Cellar")"
    # shellcheck disable=SC2155
    export HOMEBREW_REPOSITORY="$(brew --prefix --repository 2>/dev/null || echo "${HOMEBREW_PREFIX}/Homebrew")"
    export PATH="${HOMEBREW_PREFIX}/bin:${HOMEBREW_PREFIX}/sbin${PATH+:$PATH}"
    export MANPATH="${HOMEBREW_PREFIX}/share/man${MANPATH+:$MANPATH}:"
    export INFOPATH="${HOMEBREW_PREFIX}/share/info:${INFOPATH:-}"
  elif [[ "$(uname -m)" == "arm64" ]]; then
    export HOMEBREW_PREFIX="/opt/homebrew"
    export HOMEBREW_CELLAR="/opt/homebrew/Cellar"
    export HOMEBREW_REPOSITORY="/opt/homebrew"
    export PATH="/opt/homebrew/bin:/opt/homebrew/sbin${PATH+:$PATH}"
    export MANPATH="/opt/homebrew/share/man${MANPATH+:$MANPATH}:"
    export INFOPATH="/opt/homebrew/share/info:${INFOPATH:-}"
  else
    export HOMEBREW_PREFIX="/usr/local"
    export HOMEBREW_CELLAR="/usr/local/Cellar"
    export HOMEBREW_REPOSITORY="/usr/local/Homebrew"
    export PATH="/usr/local/bin:/usr/local/sbin${PATH+:$PATH}"
    export MANPATH="/usr/local/share/man${MANPATH+:$MANPATH}:"
    export INFOPATH="/usr/local/share/info:${INFOPATH:-}"
  fi

  # sublime
  export PATH="$PATH:/Applications/Sublime Text.app/Contents/SharedSupport/bin"

  # android-sdk
  export ANDROID_HOME=$HOME/Library/Android/sdk
  export ANDROID_SDK_ROOT=$ANDROID_HOME
  export PATH="$PATH:$ANDROID_SDK_ROOT/tools/bin"
  export PATH="$PATH:$ANDROID_SDK_ROOT/platform-tools"
  export PATH="$PATH:$ANDROID_SDK_ROOT/emulator"
  export PATH="$PATH:$ANDROID_SDK_ROOT/build-tools"

  # ios
  export PATH="$HOME/.fastlane/bin:$PATH"
fi

# nvm
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh" # This loads nvm
# Run compinit -i (silently ignore insecure dirs) before nvm's bash_completion
# so that nvm skips its own bare `compinit` call (which lacks -i and prompts on
# group-writable brew completion dirs after `brew bundle` runs). Running here
# also defines `compdef`, which nvm's `complete -F` needs. completions.bash
# later runs `compinit -C -i` (fast cache-hit path).
if [[ -n "${ZSH_VERSION-}" ]] && ! command -v compdef >/dev/null 2>&1; then
  autoload -Uz compinit && compinit -i -C 2>/dev/null
fi
[ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion" # This loads nvm bash_completion

# rbenv
command -v rbenv &>/dev/null && eval "$(rbenv init -)"

# rvm
export PATH="$PATH:$HOME/.rvm/bin"

# pyenv — clean stale lock before init to avoid "cannot rehash: couldn't acquire lock" errors
export PYENV_ROOT="$HOME/.pyenv"
if command -v pyenv &>/dev/null; then
  rm -f "${PYENV_ROOT}/shims/.pyenv-shim" 2>/dev/null || true
  eval "$(pyenv init -)"
fi
command -v pyenv-virtualenv-init &>/dev/null && eval "$(pyenv virtualenv-init -)"

# jenv
if command -v jenv &>/dev/null; then
  eval "$(jenv init -)"
  export PATH="$HOME/.jenv/bin:$PATH"
  # shellcheck disable=SC2155
  export JAVA_HOME="$(jenv prefix)/"
fi
