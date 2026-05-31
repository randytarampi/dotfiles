shell_is_bash=0;
shell_is_zsh=0;
case "${BASH_VERSION-}:${ZSH_VERSION-}:${0##*/}:${SHELL-}" in
	*:bash:*) shell_is_bash=1;;
	*:zsh:*) shell_is_zsh=1;;
esac;
if [ -n "${BASH_VERSION-}" ] || [ "${0##*/}" = bash ] || [[ "${SHELL-}" == */bash ]]; then
	shell_is_bash=1;
fi;
if [ -n "${ZSH_VERSION-}" ] || [ "${0##*/}" = zsh ] || [[ "${SHELL-}" == */zsh ]]; then
	shell_is_zsh=1;
fi;

[ -f $HOME/.tnsrc ] && source $HOME/.tnsrc;
[ -f $HOME/.travis/travis.sh ] && source $HOME/.travis/travis.sh;

# Use HOMEBREW_PREFIX if set (from paths.sh), otherwise fall back to uname detection
if [[ -z "${HOMEBREW_PREFIX:-}" ]]; then
	if command -v brew >/dev/null 2>&1; then
		_HB_PREFIX="$(brew --prefix)"
	elif [[ "$(uname -m)" == "arm64" ]]; then
		_HB_PREFIX="/opt/homebrew"
	else
		_HB_PREFIX="/usr/local"
	fi
else
	_HB_PREFIX="$HOMEBREW_PREFIX"
fi

if [ "$shell_is_bash" -eq 1 ]; then
	[ -f "${_HB_PREFIX}/etc/profile.d/bash_completion.sh" ] && . "${_HB_PREFIX}/etc/profile.d/bash_completion.sh";
	[ -f "/usr/share/bash-completion/bash_completion" ] && . "/usr/share/bash-completion/bash_completion";
	[ -f "/etc/bash_completion" ] && . "/etc/bash_completion";

	[ -f "${_HB_PREFIX}/Caskroom/google-cloud-sdk/latest/google-cloud-sdk/path.bash.inc" ] && source "${_HB_PREFIX}/Caskroom/google-cloud-sdk/latest/google-cloud-sdk/path.bash.inc"
fi;

if [ "$shell_is_zsh" -eq 1 ]; then
	fpath=("${_HB_PREFIX}/share/zsh-completions" $fpath);
	autoload -Uz compinit compaudit;

	# Homebrew and third-party completion directories can occasionally be
	# group/world-writable, which makes zsh prompt during startup:
	#   zsh compinit: insecure directories, run compaudit for list...
	# Repair dirs we own, then use -i so shell startup remains non-interactive
	# if any external/root-owned directory is still considered insecure.
	_insecure_completion_dirs=(${(f)"$(compaudit 2>/dev/null)"});
	for _completion_dir in "${_insecure_completion_dirs[@]}"; do
		[[ -d "$_completion_dir" && -O "$_completion_dir" ]] && chmod go-w "$_completion_dir" 2>/dev/null || true;
	done;
	unset _insecure_completion_dirs _completion_dir;

	if [[ -n ${ZDOTDIR}/.zcompdump(#qN.mh+24) ]]; then
		compinit -i;
	else
		compinit -C -i;
	fi;

	[ -f "${_HB_PREFIX}/Caskroom/google-cloud-sdk/latest/google-cloud-sdk/path.zsh.inc" ] && source "${_HB_PREFIX}/Caskroom/google-cloud-sdk/latest/google-cloud-sdk/path.zsh.inc"
fi;

unset _HB_PREFIX
