#!/usr/bin/env python3
"""
sync-brewfiles.py — Sync installed Homebrew packages into category Brewfiles.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger


@dataclass(frozen=True)
class CategorySpec:
    key: str
    filename: str
    default: bool


@dataclass
class BrewEntry:
    kind: str
    name: str
    args: str | None
    raw: str

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.name.lower()}"


@dataclass
class CategoryFile:
    spec: CategorySpec
    path: Path
    text: str
    entries: list[BrewEntry] = field(default_factory=list)
    keys: set[str] = field(default_factory=set)
    additions: list[BrewEntry] = field(default_factory=list)


CATEGORY_SPECS: list[CategorySpec] = [
    CategorySpec("dev_cli", "Brewfile", True),
    CategorySpec("dev", "Brewfile.dev", True),
    CategorySpec("desktop_browsers", "Brewfile.desktop.browsers", True),
    CategorySpec("desktop_comms", "Brewfile.desktop.comms", True),
    CategorySpec("desktop_security", "Brewfile.desktop.security", True),
    CategorySpec("desktop_media", "Brewfile.desktop.media", True),
    CategorySpec("desktop_utilities", "Brewfile.desktop.utilities", True),
    CategorySpec("desktop_fonts", "Brewfile.desktop.fonts", True),
    CategorySpec("desktop_gaming", "Brewfile.desktop.gaming", False),
    CategorySpec("desktop_cloud", "Brewfile.desktop.cloud", True),
    CategorySpec("desktop_productivity", "Brewfile.desktop.productivity", True),
    CategorySpec("desktop_dev", "Brewfile.desktop.dev", False),
    CategorySpec("dev_ops", "Brewfile.dev.ops", True),
    CategorySpec("desktop_privacy", "Brewfile.desktop.privacy", False),
    CategorySpec("desktop_emulation", "Brewfile.desktop.emulation", False),
    CategorySpec(
        "desktop_gaming_emulation", "Brewfile.desktop.gaming.emulation", False
    ),
    CategorySpec(
        "desktop_productivity_msoffice", "Brewfile.desktop.productivity.msoffice", False
    ),
    CategorySpec(
        "desktop_productivity_adobe", "Brewfile.desktop.productivity.adobe", False
    ),
    CategorySpec("legacy", "Brewfile.legacy", False),
    CategorySpec("legacy_macfuse", "Brewfile.legacy.macfuse", False),
    CategorySpec("legacy_vmware", "Brewfile.legacy.vmware", False),
]

ENTRY_RE = re.compile(r'^(tap|brew|cask|npm)\s+"([^"]+)"(?:\s*,\s*(.*))?$')

TAP_CATEGORY = "dev_cli"
CORE_CLI_FORMULAS = {
    "bash",
    "chezmoi",
    "coreutils",
    "curl",
    "gh",
    "git",
    "git-filter-repo",
    "git-lfs",
    "go",
    "htop",
    "jq",
    "make",
    "node",
    "nvm",
    "p7zip",
    "ripgrep",
    "starship",
    "tmux",
    "uv",
    "wget",
    "yq",
}

DEV_FORMULA_EXACT = {
    "alpine",
    "ant",
    "autoconf",
    "automake",
    "black",
    "cask",
    "cmake",
    "circleci",
    "clamav",
    "cppcheck",
    "croc",
    "cython",
    "diskonaut",
    "emacs",
    "erlang",
    "espeak-ng",
    "exiftool",
    "faac",
    "fastfetch",
    "fastlane",
    "ffmpeg",
    "ffmpeg@4",
    "ffuf",
    "felinks",
    "flashrom",
    "gauge",
    "gemini-cli",
    "gettext",
    "gh",
    "glow",
    "gradle",
    "gradle-completion",
    "graphviz",
    "gptfdisk",
    "gdal",
    "gd",
    "hicolor-icon-theme",
    "httpie",
    "hyfetch",
    "icu4c@78",
    "imagemagick",
    "jenv",
    "jasper",
    "libevent",
    "libgsm",
    "libheif",
    "libicns",
    "libimobiledevice",
    "libksba",
    "libpq",
    "librsvg",
    "librttopo",
    "libusb",
    "libusb-compat",
    "libvo-aacenc",
    "libxml2",
    "lynx",
    "maven",
    "makedepend",
    "mapnik",
    "meson",
    "miniupnpc",
    "nb",
    "net-snmp",
    "nghttp2",
    "nim",
    "nnn",
    "nss",
    "numpy",
    "opencv",
    "openrtsp",
    "parallel",
    "pandoc",
    "p7zip",
    "php",
    "pipenv",
    "pipx",
    "pinentry",
    "pinentry-mac",
    "pkgconf",
    "powershell",
    "pre-commit",
    "pyenv-virtualenv",
    "pyqt",
    "python",
    "python@3.10",
    "python@3.11",
    "qt",
    "r",
    "rbenv",
    "ripgrep",
    "ruby-build",
    "rust",
    "sane-backends",
    "scons",
    "shellcheck",
    "shfmt",
    "sox",
    "speex",
    "svgo",
    "swig",
    "tfenv",
    "testdisk",
    "travis",
    "uv",
    "vale",
    "vtk",
    "watchman",
    "whisper-cpp",
    "wimlib",
    "zlib",
}

DEV_FORMULA_PREFIXES = (
    "python@",
    "ruby",
)

DEV_OPS_FORMULAS = {
    "ansible",
    "arping",
    "awscli",
    "caddy",
    "certbot",
    "cloudflare-speed-cli",
    "duckdb",
    "fping",
    "gcloud",
    "gcloud-cli",
    "glances",
    "hf",
    "hey",
    "helm",
    "inetutils",
    "iperf3",
    "iproute2mac",
    "k6",
    "kubectl",
    "kubernetes-cli",
    "libgphoto2",
    "mailutils",
    "mongosh",
    "postgresql@16",
    "ssh-audit",
    "terraform",
    "speed-cloudflare-cli",
}

BROWSER_CASK_HINTS = (
    "firefox",
    "chrome",
    "edge",
    "brave",
    "opera",
    "vivaldi",
    "safari-technology-preview",
    "chromium",
)

COMM_CASK_HINTS = (
    "discord",
    "slack",
    "signal",
    "skype",
    "teams",
    "telegram",
    "whatsapp",
    "webex",
    "zoom",
)

SECURITY_CASK_HINTS = (
    "tunnelblick",
    "wireguard",
    "aws-vpn-client",
    "gpg-suite",
    "1password",
)

MEDIA_CASK_HINTS = (
    "handbrake",
    "iina",
    "spotify",
    "vlc",
    "vlc-streamer",
)

UTILITY_CASK_HINTS = (
    "alt-tab",
    "appcleaner",
    "bettertouchtool",
    "karabiner",
    "macfuse",
    "rectangle",
)

FONT_CASK_PREFIX = "font-"

CLOUD_CASK_HINTS = (
    "dropbox",
    "google-drive",
    "icloud",
    "nextcloud",
)

PRODUCTIVITY_CASK_HINTS = (
    "office",
    "notion",
    "obsidian",
    "things",
    "todoist",
)

GAMING_CASK_HINTS = (
    "battle-net",
    "epic-games",
    "gog-galaxy",
    "heroic",
    "minecraft",
    "steam",
)

PRIVACY_CASK_HINTS = (
    "mullvad-browser",
    "mullvadvpn",
    "mullvad-vpn",
    "private-internet-access",
)

EMULATION_CASK_HINTS = (
    "parallels",
    "utm",
    "vmware-fusion",
)

GAMING_EMULATION_CASK_HINTS = (
    "dolphin",
    "pcsx2",
)

PRODUCTIVITY_MSOFFICE_CASK_HINTS = (
    "microsoft-auto-update",
    "microsoft-office",
    "microsoft-teams",
)

PRODUCTIVITY_ADOBE_CASK_HINTS = (
    "adobe-creative-cloud",
    "adobe-acrobat",
)

DESKTOP_DEV_CASK_HINTS = (
    "docker-desktop",
    "iterm2",
    "jetbrains-toolbox",
    "oracle-jdk",
    "sublime-text",
    "visual-studio-code",
    "ungoogled-chromium",
    "antigravity",
    "opencode-desktop",
    "ollama-app",
    "codexbar",
    "itermai",
    "siliconscope",
    "mx-power-gadget",
    "mongodb-compass",
    "session-manager-plugin",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync installed Homebrew packages into category Brewfiles."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show changes without writing files."
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Auto-assign entries using heuristics when possible.",
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="Report installed/file differences and exit 1 on mismatch.",
    )
    return parser.parse_args()


def load_entry_lines(text: str) -> list[BrewEntry]:
    entries: list[BrewEntry] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = ENTRY_RE.match(stripped)
        if not match:
            continue
        kind, name, args = match.groups()
        entries.append(BrewEntry(kind=kind, name=name, args=args, raw=stripped))
    return entries


def load_category_files(dotfiles_dir: Path) -> dict[str, CategoryFile]:
    category_files: dict[str, CategoryFile] = {}
    for spec in CATEGORY_SPECS:
        path = dotfiles_dir / spec.filename
        text = ""
        if path.exists():
            text = path.read_text(encoding="utf-8")
        entries = load_entry_lines(text)
        keys = {entry.key for entry in entries}
        category_files[spec.key] = CategoryFile(
            spec=spec,
            path=path,
            text=text,
            entries=entries,
            keys=keys,
        )
    return category_files


def run_brew_bundle_dump() -> str | None:
    if shutil.which("brew") is None:
        logger.warning("Homebrew is not available; skipping Brewfile sync.")
        return None

    result = subprocess.run(
        ["brew", "bundle", "dump", "--file=-"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = (result.stderr or result.stdout or "").strip()
        if stderr:
            logger.warning(f"brew bundle dump failed ({result.returncode}): {stderr}")
        else:
            logger.warning(f"brew bundle dump failed ({result.returncode}).")
        return None

    return result.stdout


def format_entry(entry: BrewEntry) -> str:
    if entry.args:
        return f'{entry.kind} "{entry.name}", {entry.args}'
    return f'{entry.kind} "{entry.name}"'


def normalize_name(value: str) -> str:
    return value.lower()


def _matches_hint(name: str, hints: tuple[str, ...]) -> bool:
    lowered = normalize_name(name)
    return any(hint in lowered for hint in hints)


def classify_entry(entry: BrewEntry) -> str | None:
    name = normalize_name(entry.name)

    if entry.kind == "tap":
        return TAP_CATEGORY

    if entry.kind == "cask":
        if name.startswith(FONT_CASK_PREFIX):
            return "desktop_fonts"
        if _matches_hint(name, BROWSER_CASK_HINTS):
            return "desktop_browsers"
        if _matches_hint(name, COMM_CASK_HINTS):
            return "desktop_comms"
        if _matches_hint(name, SECURITY_CASK_HINTS):
            return "desktop_security"
        if _matches_hint(name, MEDIA_CASK_HINTS):
            return "desktop_media"
        if _matches_hint(name, UTILITY_CASK_HINTS):
            return "desktop_utilities"
        if _matches_hint(name, CLOUD_CASK_HINTS):
            return "desktop_cloud"
        if _matches_hint(name, PRODUCTIVITY_CASK_HINTS):
            return "desktop_productivity"
        if _matches_hint(name, GAMING_CASK_HINTS):
            return "desktop_gaming"
        if _matches_hint(name, PRIVACY_CASK_HINTS):
            return "desktop_privacy"
        if _matches_hint(name, EMULATION_CASK_HINTS):
            return "desktop_emulation"
        if _matches_hint(name, GAMING_EMULATION_CASK_HINTS):
            return "desktop_gaming_emulation"
        if _matches_hint(name, PRODUCTIVITY_MSOFFICE_CASK_HINTS):
            return "desktop_productivity_msoffice"
        if _matches_hint(name, PRODUCTIVITY_ADOBE_CASK_HINTS):
            return "desktop_productivity_adobe"
        if _matches_hint(name, DESKTOP_DEV_CASK_HINTS):
            return "desktop_dev"
        return None

    if entry.kind == "brew":
        if name in CORE_CLI_FORMULAS:
            return "dev_cli"
        if name in DEV_OPS_FORMULAS:
            return "dev_ops"
        if name in DEV_FORMULA_EXACT or any(
            name.startswith(prefix) for prefix in DEV_FORMULA_PREFIXES
        ):
            return "dev"
        return None

    if entry.kind == "npm":
        return "dev"

    return None


def add_entry(
    category_files: dict[str, CategoryFile], entry: BrewEntry, category_key: str
) -> bool:
    category_file = category_files[category_key]
    if entry.key in category_file.keys:
        return False
    category_file.entries.append(entry)
    category_file.additions.append(entry)
    category_file.keys.add(entry.key)
    return True


def prompt_for_category(entry: BrewEntry, suggestion: str | None) -> str:
    logger.info(f"Unassigned entry: {format_entry(entry)}")
    if suggestion:
        logger.info(f"Suggested category: {suggestion}")
    for index, spec in enumerate(CATEGORY_SPECS, start=1):
        default_flag = " [default]" if spec.default else ""
        print(f"  {index}. {spec.key} ({spec.filename}){default_flag}")

    valid_numbers = {
        str(index): spec.key for index, spec in enumerate(CATEGORY_SPECS, start=1)
    }
    valid_keys = {spec.key: spec.key for spec in CATEGORY_SPECS}

    while True:
        prompt = "Assign to category (number/key, 's' to skip, 'q' to quit)"
        if suggestion:
            prompt += f" [{suggestion}]"
        prompt += ": "

        try:
            choice = input(prompt).strip().lower()
        except EOFError:
            logger.warning(
                "Input closed while prompting; leaving remaining entries unassigned."
            )
            return ""

        if not choice and suggestion:
            return suggestion
        if choice in {"q", "quit"}:
            raise KeyboardInterrupt
        if choice in {"s", "skip"}:
            return ""
        if choice in valid_numbers:
            return valid_numbers[choice]
        if choice in valid_keys:
            return valid_keys[choice]

        logger.warning("Invalid selection. Please choose a number or category key.")


def write_category_files(category_files: dict[str, CategoryFile]) -> list[Path]:
    updated_paths: list[Path] = []
    for spec in CATEGORY_SPECS:
        category_file = category_files[spec.key]
        if not category_file.additions:
            continue

        addition_lines = [format_entry(entry) for entry in category_file.additions]
        existing_text = category_file.text
        if existing_text and not existing_text.endswith("\n"):
            existing_text += "\n"
        if existing_text and not existing_text.endswith("\n\n"):
            existing_text += "\n"
        new_text = existing_text + "\n".join(addition_lines) + "\n"

        category_file.path.write_text(new_text, encoding="utf-8")
        updated_paths.append(category_file.path)
    return updated_paths


def report_diff(
    installed_entries: list[BrewEntry], category_files: dict[str, CategoryFile]
) -> int:
    installed_keys = {entry.key for entry in installed_entries}
    category_key_to_files: dict[str, list[str]] = {}
    for spec in CATEGORY_SPECS:
        category_file = category_files[spec.key]
        for key in category_file.keys:
            category_key_to_files.setdefault(key, []).append(spec.key)

    installed_missing = [
        entry for entry in installed_entries if entry.key not in category_key_to_files
    ]
    file_extra: dict[str, list[str]] = {}
    for spec in CATEGORY_SPECS:
        category_file = category_files[spec.key]
        extras = sorted(key for key in category_file.keys if key not in installed_keys)
        if extras:
            file_extra[spec.key] = extras

    if not installed_missing and not file_extra:
        logger.info("Installed packages match all Brewfiles.")
        return 0

    if installed_missing:
        logger.warning("Installed but not in any Brewfile:")
        for entry in installed_missing:
            logger.warning(f"  - {format_entry(entry)}")

    if file_extra:
        logger.warning("In Brewfiles but not installed:")
        for category_key, keys in file_extra.items():
            logger.warning(f"  {category_key}:")
            for key in keys:
                category_file = category_files[category_key]
                entry = next(
                    (item for item in category_file.entries if item.key == key), None
                )
                logger.warning(f"    - {entry.raw if entry else key}")

    return 1


def sync_brewfiles(args: argparse.Namespace) -> int:
    dotfiles_dir = Path(os.path.dirname(SCRIPT_DIR))
    dump = run_brew_bundle_dump()
    if dump is None:
        return 0

    installed_entries = load_entry_lines(dump)
    category_files = load_category_files(dotfiles_dir)
    assigned_keys = {
        key for category_file in category_files.values() for key in category_file.keys
    }

    if args.diff:
        return report_diff(installed_entries, category_files)

    unassigned: list[BrewEntry] = []
    for entry in installed_entries:
        if entry.key in assigned_keys:
            continue

        suggestion = classify_entry(entry)
        if args.auto and suggestion:
            if add_entry(category_files, entry, suggestion):
                assigned_keys.add(entry.key)
            logger.info(f"Auto-assigned {format_entry(entry)} -> {suggestion}")
            continue

        if suggestion and not args.auto:
            logger.info(
                f"Heuristic suggestion for {format_entry(entry)} -> {suggestion}"
            )

        unassigned.append(entry)

    try:
        for entry in unassigned:
            suggestion = classify_entry(entry)
            category_key = prompt_for_category(entry, suggestion)
            if not category_key:
                logger.warning(f"Skipped {format_entry(entry)}")
                continue
            if add_entry(category_files, entry, category_key):
                assigned_keys.add(entry.key)
            logger.info(f"Assigned {format_entry(entry)} -> {category_key}")
    except KeyboardInterrupt:
        logger.warning("Stopped by user; writing any completed assignments only.")

    if not any(category_file.additions for category_file in category_files.values()):
        logger.info("No Brewfile updates required.")
        return 0

    if args.dry_run:
        logger.info("Dry-run: would update the following Brewfiles:")
        for spec in CATEGORY_SPECS:
            category_file = category_files[spec.key]
            if not category_file.additions:
                continue
            logger.info(f"  {spec.filename}:")
            for entry in category_file.additions:
                logger.info(f"    + {format_entry(entry)}")
        return 0

    updated_paths = write_category_files(category_files)
    for path in updated_paths:
        logger.info(f"Updated {path.name}")
    return 0


def main() -> int:
    args = parse_args()
    return sync_brewfiles(args)


if __name__ == "__main__":
    sys.exit(main())
