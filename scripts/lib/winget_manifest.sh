#!/usr/bin/env bash
# Install plain package identifiers from a dotfiles wingetfile.
# The WinGet "import" subcommand accepts JSON exports, not this repository's
# intentionally reviewable, comment-friendly one-ID-per-line manifests.
# Loaded by the Windows package lane; it does not declare services.

wingetfile_install() {
  local file="$1"
  local package failed=0
  local ignored=",${DOTFILES_WINGETFILE_IGNORE:-},"
  ignored="${ignored//[[:space:]]/}"

  if [[ ! -r "$file" ]]; then
    warn "Cannot read wingetfile: $file"
    return 1
  fi

  while IFS= read -r package || [[ -n "$package" ]]; do
    # Preserve CRLF compatibility and permit blank/comment lines.
    package="${package%$'\r'}"
    [[ "$package" =~ ^[[:space:]]*$ ]] && continue
    [[ "$package" =~ ^[[:space:]]*# ]] && continue

    # Package IDs are data, never shell, patterns or CLI flags.
    if [[ ! "$package" =~ ^[A-Za-z0-9][A-Za-z0-9._+-]*$ ]]; then
      warn "Invalid WinGet package ID in $file: $package"
      failed=$((failed + 1))
      continue
    fi

    # Exact comma-separated token matching (no grep regex interpretation).
    if [[ "$ignored" == *,"$package",* ]]; then
      continue
    fi

    if ! winget install --id "$package" --exact --accept-package-agreements --accept-source-agreements --disable-interactivity; then
      warn "WinGet installation failed for $package"
      failed=$((failed + 1))
    fi
  done <"$file"

  if ((failed > 0)); then
    warn "$failed WinGet package(s) could not be installed from $file"
    return 1
  fi
}
