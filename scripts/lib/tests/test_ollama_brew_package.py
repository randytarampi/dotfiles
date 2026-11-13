"""Isolate opt-in Ollama formula installation from desktop-app ownership."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PREFLIGHT = ROOT / "scripts/lib/ollama_brew_preflight.sh"


def run_preflight(tmp_path, system="Darwin", desktop_dev="false", cask=""):
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    for name, body in {
        "uname": f"#!/bin/sh\nprintf '%s\\n' {system}\n",
        "brew": (
            "#!/bin/sh\n"
            'if [ "$1" = "list" ] && [ "$2" = "--cask" ] '
            '&& [ "$3" = "--versions" ] && [ "$4" = "$CASK_TEST" ]; '
            "then printf 'installed\\n'; fi\n"
        ),
    }.items():
        tool = stub / name
        tool.write_text(body, encoding="utf-8")
        tool.chmod(0o755)
    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": str(stub) + os.pathsep + os.environ.get("PATH", ""),
        "PREFLIGHT": str(PREFLIGHT),
        "CASK_TEST": cask,
        "DESKTOP_DEV": desktop_dev,
        # Hosts with /Applications/Ollama.app must not fail the hermetic
        # cases; point the preflight at the isolated, empty mount root.
        "OLLAMA_BREW_PREFLIGHT_APPLICATIONS_DIR": str(tmp_path / "apps"),
    }
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$PREFLIGHT"; ollama_brew_package_preflight "$DESKTOP_DEV"',
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, home


def test_absolute_applications_override_still_refuses_present_app(tmp_path):
    apps = tmp_path / "apps"
    apps.mkdir()
    (apps / "Ollama.app").mkdir()
    result, home = run_preflight(tmp_path)
    assert result.returncode == 1, result.stderr
    assert "Ollama.app exists" in result.stderr
    # The HOME-relative path is honoured without the override directory.
    shutil.rmtree(apps)
    (home / "Applications").mkdir()
    (home / "Applications" / "Ollama.app").mkdir()
    result, _ = run_preflight(tmp_path)
    assert result.returncode == 1, result.stderr
    assert "Ollama.app exists" in result.stderr


def test_headless_opt_in_passes_without_desktop_install(tmp_path):
    result, _ = run_preflight(tmp_path)
    assert result.returncode == 0, result.stderr


def test_selected_desktop_category_refuses_formula(tmp_path):
    result, _ = run_preflight(tmp_path, desktop_dev="true")
    assert result.returncode == 1
    assert "desktop_dev" in result.stderr


@pytest.mark.parametrize("cask", ["ollama-app", "ollama-binary"])
def test_installed_homebrew_cask_refuses_formula(tmp_path, cask):
    result, _ = run_preflight(tmp_path, cask=cask)
    assert result.returncode == 1
    assert cask in result.stderr


def test_standalone_app_in_home_refuses_formula(tmp_path):
    first, home = run_preflight(tmp_path)
    assert first.returncode == 0
    (home / "Applications/Ollama.app").mkdir(parents=True)
    result, _ = run_preflight(tmp_path)
    assert result.returncode == 1
    assert "Ollama.app exists" in result.stderr


def test_linux_not_blocked_by_macos_desktop_category(tmp_path):
    result, _ = run_preflight(tmp_path, system="Linux", desktop_dev="true")
    assert result.returncode == 0, result.stderr


def test_new_category_only_installs_and_does_not_start_service():
    template = (
        ROOT / ".chezmoiscripts/run_onchange_04-install-packages.sh.tmpl"
    ).read_text(encoding="utf-8")
    manifest = (ROOT / "Brewfile.local.ai.headless").read_text(encoding="utf-8")
    categories = (ROOT / ".chezmoidata/categories.yaml").read_text(encoding="utf-8")

    assert "  local_ai_headless: false" in categories
    assert 'brew "ollama"' in manifest
    assert "restart_service" not in manifest
    assert "brew services start ollama" not in template
    assert template.index("ollama_brew_package_preflight") < template.index(
        "brew_bundle_filtered {{ .chezmoi.sourceDir }}/Brewfile"
    )
    assert "wingetfile.local.ai.headless" in template
