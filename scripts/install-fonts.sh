#!/usr/bin/env bash
set -euo pipefail

_SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
LIB_DIR="$SCRIPT_DIR/lib"

# shellcheck disable=SC1091
source "$LIB_DIR/common.sh"
source "$LIB_DIR/common_args.sh"
export COMMON_USAGE="$0 <linux|windows>"
export COMMON_HELP_TEXT="Install the configured developer fonts for the selected platform."
parse_common_args "$@"
set -- "${COMMON_ARGS_REMAINING[@]}"

MESLO_DZ_FALLBACK_URL="https://github.com/andreberg/Meslo-Font/raw/master/dist/v1.2.1/Meslo%20LG%20DZ%20v1.2.1.zip"
MESLO_DZ_URL="$MESLO_DZ_FALLBACK_URL"
JETBRAINS_MONO_URL="https://github.com/JetBrains/JetBrainsMono/releases/latest/download/JetBrainsMono.zip"
MESLO_NERD_URL="https://github.com/ryanoasis/nerd-fonts/releases/latest/download/Meslo.zip"
SOURCE_CODE_PRO_URL="https://github.com/adobe-fonts/source-code-pro/releases/latest/download/TTF.zip"

download_and_extract_font() {
  local name="$1" url="$2" dest_dir="$3"
  local tmp_zip="/tmp/${name}.zip"

  if [[ -d "$dest_dir" ]] && ls "$dest_dir"/*.ttf &>/dev/null; then
    info "$name already installed (found .ttf files in $dest_dir)"
    return 0
  fi

  info "Downloading $name..."
  mkdir -p "$dest_dir"
  curl -fsSL "$url" -o "$tmp_zip" || {
    warn "Failed to download $name from $url"
    return 0
  }
  unzip -jo "$tmp_zip" '*.ttf' -d "$dest_dir" 2>/dev/null ||
    unzip -jo "$tmp_zip" -d "$dest_dir" 2>/dev/null ||
    warn "Failed to extract $name"
  rm -f "$tmp_zip"
}

resolve_meslo_dz_url() {
  local release_json asset_url

  if ! command -v python3 &>/dev/null; then
    warn "python3 not found; using pinned Meslo LG DZ download URL"
    return 0
  fi

  if ! release_json="$(curl -fsSL "https://api.github.com/repos/andreberg/Meslo-Font/releases/latest" 2>/dev/null)"; then
    warn "Could not query the Meslo-Font latest release; using pinned download URL"
    return 0
  fi

  asset_url="$(printf '%s' "$release_json" | python3 -c '
import json
import sys

try:
    release = json.load(sys.stdin)
    for asset in release.get("assets", []):
        name = asset.get("name", "").lower()
        if "meslo lg dz" in name and name.endswith(".zip"):
            print(asset["browser_download_url"])
            break
except (json.JSONDecodeError, KeyError, TypeError):
    pass
')" || asset_url=""

  if [[ -n "$asset_url" ]]; then
    MESLO_DZ_URL="$asset_url"
    info "Resolved latest Meslo LG DZ release asset"
  else
    warn "No Meslo LG DZ release asset found; using pinned download URL"
  fi
}

install_linux() {
  local font_root="$HOME/.local/share/fonts"
  local package_manager=""

  if command -v apt-get &>/dev/null; then
    package_manager="apt-get"
    sudo apt-get install -y fonts-jetbrains-mono fonts-source-code-pro ||
      warn "apt-get font installation failed; continuing with manual downloads"
  elif command -v dnf &>/dev/null; then
    package_manager="dnf"
    sudo dnf install -y jetbrains-mono-fonts adobe-source-code-pro-fonts ||
      warn "dnf font installation failed; continuing with manual downloads"
  else
    warn "Neither apt-get nor dnf found; downloading package fonts manually"
  fi

  resolve_meslo_dz_url
  download_and_extract_font "meslo-lg-dz" "$MESLO_DZ_URL" "$font_root/meslo-lg-dz"
  download_and_extract_font "meslo-nerd" "$MESLO_NERD_URL" "$font_root/meslo-nerd"

  if command -v fc-cache &>/dev/null; then
    fc-cache -f || warn "fc-cache failed"
  else
    warn "fc-cache not found; refresh the font cache manually"
  fi

  ok "Fonts installed for Linux.

Package manager: ${package_manager:-none}
Manual fonts: Meslo LG DZ, Meslo LG Nerd Font
Font directory: $font_root"
}

install_windows() {
  local temp_dir powershell_script

  temp_dir="$(mktemp -d)"
  trap 'rm -rf "$temp_dir"' RETURN

  resolve_meslo_dz_url
  download_and_extract_font "meslo-lg-dz" "$MESLO_DZ_URL" "$temp_dir/meslo-lg-dz"
  download_and_extract_font "jetbrains-mono" "$JETBRAINS_MONO_URL" "$temp_dir/jetbrains-mono"
  download_and_extract_font "meslo-nerd" "$MESLO_NERD_URL" "$temp_dir/meslo-nerd"
  download_and_extract_font "source-code-pro" "$SOURCE_CODE_PRO_URL" "$temp_dir/source-code-pro"

  powershell_script=$(
    cat <<EOF
\$sourceDir = '$temp_dir'
\$fontDir = "\$env:LOCALAPPDATA\\Microsoft\\Windows\\Fonts"
New-Item -ItemType Directory -Force \$fontDir | Out-Null
\$regPath = "HKCU:\\Software\\Microsoft\\Windows NT\\CurrentVersion\\Fonts"
New-Item -Path \$regPath -Force | Out-Null
Get-ChildItem -Path \$sourceDir -Recurse -Filter *.ttf | ForEach-Object {
    \$dest = Join-Path \$fontDir \$_.Name
    Copy-Item \$_.FullName \$dest -Force
    New-ItemProperty -Path \$regPath -Name \$_.Name -Value \$dest -PropertyType String -Force | Out-Null
}
EOF
  )

  if powershell.exe -NoProfile -Command "$powershell_script"; then
    ok "Windows fonts copied and registered for the current user"
  else
    warn "PowerShell font registration failed"
  fi

  ok "Fonts installed for Windows.

Manual fonts: Meslo LG DZ, JetBrains Mono, Meslo LG Nerd Font, Source Code Pro
Registration: HKCU per-user font registry"
}

main() {
  local platform="${1:-}"

  if [[ "$#" -ne 1 || ("$platform" != "linux" && "$platform" != "windows") ]]; then
    die "Usage: $0 <linux|windows>"
  fi
  command -v curl &>/dev/null || die "'curl' not found; install it first"
  command -v unzip &>/dev/null || die "'unzip' not found; install it first"

  case "$platform" in
  linux)
    install_linux
    ;;
  windows)
    command -v powershell.exe &>/dev/null || die "'powershell.exe' not found; install or enable PowerShell first"
    install_windows
    ;;
  esac
}

main "$@"
