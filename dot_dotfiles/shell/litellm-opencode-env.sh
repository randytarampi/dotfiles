# shellcheck shell=bash
# Export the managed OpenCode client key only for fully opted-in shell processes.
dotfiles_load_litellm_opencode_key() {
  [[ "${DOTFILES_USE_LITELLM_PROXY:-0}" == "1" &&
    "${DOTFILES_RUN_OPENCODE_SETUP:-0}" == "1" &&
    "${DOTFILES_RUN_LITELLM_SETUP:-0}" == "1" ]] || return 0
  [[ -n "${LITELLM_OPENCODE_KEY:-}" ]] && return 0

  local key_file="$HOME/.local/share/litellm/clients/opencode.key"
  local directory="$HOME"
  local component
  [[ ! -L "$key_file" && -f "$key_file" && -r "$key_file" ]] || return 0
  for component in .local share litellm clients; do
    directory="$directory/$component"
    [[ ! -L "$directory" && -d "$directory" ]] || return 0
  done
  local metadata owner mode
  metadata="$(stat -f '%u %Lp' "$key_file" 2>/dev/null)" ||
    metadata="$(stat -c '%u %a' "$key_file" 2>/dev/null)" || return 0
  read -r owner mode <<<"$metadata"
  [[ "$owner" == "$(id -u)" && "$mode" == "600" ]] || return 0

  LITELLM_OPENCODE_KEY="$(<"$key_file")"
  [[ -n "$LITELLM_OPENCODE_KEY" ]] || return 0
  export LITELLM_OPENCODE_KEY
}

dotfiles_load_litellm_opencode_key
unset -f dotfiles_load_litellm_opencode_key
