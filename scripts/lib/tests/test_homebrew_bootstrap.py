"""Offline first-install Homebrew tests; never install system packages."""

import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "scripts/lib/homebrew_bootstrap.sh"
TEMPLATE = ROOT / ".chezmoiscripts/run_onchange_04-install-packages.sh.tmpl"


def _scenario(
    tmp_path, *, existing=False, curl_exit=0, installer_exit=0, shellenv_exit=0
):
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fixture = tmp_path / "fixture-brew"
    fixture.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == shellenv ]]; then\n'
        '  [[ "$FAKE_SHELLENV_EXIT" == 0 ]] || exit "$FAKE_SHELLENV_EXIT"\n'
        '  printf \'export PATH="%s:$PATH"\\n\' "$(dirname "$0")"\n'
        "else\n"
        '  printf "%s\\n" "$*" >> "$BREW_CALLS"\n'
        "fi\n",
        encoding="utf-8",
    )
    fixture.chmod(0o755)
    if existing:
        target = home / ".linuxbrew/bin/brew"
        target.parent.mkdir(parents=True)
        target.write_bytes(fixture.read_bytes())
        target.chmod(0o755)
    curl = bin_dir / "curl"
    curl.write_text(
        "#!/usr/bin/env bash\n"
        'printf "curl\\n" >> "$BREW_CALLS"\n'
        '[[ "$FAKE_CURL_EXIT" == 0 ]] || exit "$FAKE_CURL_EXIT"\n'
        'printf \'[[ "$FAKE_INSTALLER_EXIT" == 0 ]] || exit "$FAKE_INSTALLER_EXIT"; '
        'mkdir -p "$HOME/.linuxbrew/bin"; '
        'cp "$BREW_FIXTURE" "$HOME/.linuxbrew/bin/brew"; '
        'chmod +x "$HOME/.linuxbrew/bin/brew"\\n\'\n',
        encoding="utf-8",
    )
    curl.chmod(0o755)
    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "BREW_FIXTURE": str(fixture),
        "BREW_CALLS": str(tmp_path / "calls"),
        "FAKE_CURL_EXIT": str(curl_exit),
        "FAKE_INSTALLER_EXIT": str(installer_exit),
        "FAKE_SHELLENV_EXIT": str(shellenv_exit),
        "HOMEBREW_PREFIX": str(home / ".linuxbrew"),
    }
    # A macOS CI runner already has /opt/homebrew/bin/brew. Override only
    # binary discovery for this scenario to exercise the *absent* host branch.
    # Production discovery is tested separately below.
    shell = (
        "set -euo pipefail\n"
        'source "$1"\n'
        "_dotfiles_brew_binary() {\n"
        '  [[ -x "$HOME/.linuxbrew/bin/brew" ]] || return 1\n'
        '  printf "%s\\n" "$HOME/.linuxbrew/bin/brew"\n'
        "}\n"
        "dotfiles_homebrew_ready\n"
        "command -v brew\n"
    )
    result = subprocess.run(
        ["bash", "-c", shell, "bash", str(HELPER)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    calls_path = tmp_path / "calls"
    calls = calls_path.read_text().splitlines() if calls_path.exists() else []
    return result, calls, home


def test_first_install_discovers_new_binary(tmp_path):
    result, calls, home = _scenario(tmp_path)
    assert result.returncode == 0, result.stderr
    assert str(home / ".linuxbrew/bin/brew") in result.stdout
    assert calls == ["curl"]


def test_existing_binary_does_not_download_installer(tmp_path):
    result, calls, _ = _scenario(tmp_path, existing=True)
    assert result.returncode == 0, result.stderr
    assert calls == []


def test_download_failure_refuses_install(tmp_path):
    result, calls, home = _scenario(tmp_path, curl_exit=22)
    assert result.returncode != 0
    assert "Cannot download" in result.stderr
    assert calls == ["curl"]
    assert not (home / ".linuxbrew/bin/brew").exists()


def test_failed_installer_refuses_bundle(tmp_path):
    result, calls, home = _scenario(tmp_path, installer_exit=7)
    assert result.returncode != 0
    assert "Homebrew installation failed" in result.stderr
    assert calls == ["curl"]
    assert not (home / ".linuxbrew/bin/brew").exists()


def test_shellenv_failure_refuses_bundle(tmp_path):
    result, calls, _ = _scenario(tmp_path, existing=True, shellenv_exit=8)
    assert result.returncode != 0
    assert "Homebrew shellenv failed" in result.stderr
    assert calls == []


def test_template_hash_and_entrypoint_are_connected():
    source = TEMPLATE.read_text()
    assert 'source "${LIB_DIR}/homebrew_bootstrap.sh"' in source
    assert '{{ include "scripts/lib/homebrew_bootstrap.sh" | sha256sum }}' in source
    assert 'BREW_BIN="${BREW_PREFIX}/bin/brew"' not in source


def test_real_discovery_checks_explicit_prefix(tmp_path):
    home = tmp_path / "isolated-home"
    home.mkdir()
    prefix = home / ".linuxbrew"
    executable = prefix / "bin/brew"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    env = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin",
        "HOMEBREW_PREFIX": str(prefix),
    }
    result = subprocess.run(
        ["bash", "-c", 'source "$1"; _dotfiles_brew_binary', "bash", str(HELPER)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(executable)
