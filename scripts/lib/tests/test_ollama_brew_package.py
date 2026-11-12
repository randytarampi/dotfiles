"""Isolate the opt-in Ollama formula package lane from desktop-app ownership."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PREFLIGHT = ROOT / "scripts/lib/ollama_brew_preflight.sh"


def run_preflight(tmp_path, system="Darwin", desktop_dev="false", cask=""):
    stub = tmp_path / "bin"
    stub.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    for name, body in {
        "uname": f"#!/bin/sh\\nprintf '%s\\n' {system}\\n",
        "brew": (
            "#!/bin/sh\\n"
            'if [ "$1" = "list" ] && [ "$2" = "--cask" ] '
            '&& [ "$3" = "--versions" ] && [ "$4" = "$CASK_TEST" ]; '
            "then printf 'installed\\n'; fi\\n"
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
    }
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$PREFLIGHT"; ollama_brew_package_preflight "$DESKTOP_DEV"',
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    ), home


def test_headless_opt_in_passes_without_desktop_install(tmp_path):
    result, _ = run_preflight(tmp_path)
    assert result.returncode == 0, result.stderr


def test_selected_desktop_category_refuses_formula(tmp_path):
    result, _ = run_preflight(tmp_path, desktop_dev="true")
    assert result.returncode == 1
    assert "desktop_dev" in result.stderr


def test_installed_homebrew_app_cask_refuses_formula(tmp_path):
    result, _ = run_preflight(tmp_path, cask="ollama-app")
    assert result.returncode == 1
    assert "ollama-app" in result.stderr


def test_standalone_app_in_home_refuses_formula(tmp_path):
    result, home = run_preflight(tmp_path)
    assert result.returncode == 0
    (home / "Applications/Ollama.app").mkdir(parents=True)
    env = {**os.environ, "HOME": str(home), "PREFLIGHT": str(PREFLIGHT)}
    # File-level check exercises an actual app directory, not just cask metadata.
    script = PREFLIGHT.read_text(encoding="utf-8")
    assert '$HOME/Applications/Ollama.app' in script


def test_linux_is_not_blocked_by_macos_desktop_category(tmp_path):
    result, _ = run_preflight(tmp_path, system="Linux", desktop_dev="true")
    assert result.returncode == 0, result.stderr


def test_new_category_never_installs_or_starts_ollama_implicitly():
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
