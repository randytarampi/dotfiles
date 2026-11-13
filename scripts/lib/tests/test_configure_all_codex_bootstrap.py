"""Core-only Codex bootstrap regression through the actual configure-all orchestration."""

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/configure-all.sh"


def _run(tmp_path, *, installed=False, existing=False, requested=False, skip=False):
    home = tmp_path / "home"
    home.mkdir()
    binaries = tmp_path / "bin"
    binaries.mkdir()
    if installed:
        codex = binaries / "codex"
        codex.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        codex.chmod(0o755)
    config = home / ".codex/config.toml"
    if existing:
        config.parent.mkdir()
        config.write_text("# user-owned profile\n", encoding="utf-8")

    # No Poetry in PATH; execute the real orchestrator with synthetic HOME.
    env = {
        "HOME": str(home),
        "PATH": f"{binaries}:{Path(sys.executable).parent}:/usr/bin:/bin",
        "DOTFILES_RUN_CODEX_SETUP": "1" if requested else "0",
        "DOTFILES_RUN_OPENCODE_SETUP": "0",
        "DOTFILES_RUN_LITELLM_SETUP": "0",
        "DOTFILES_RUN_PACKAGES_SETUP": "0",
        "NO_COLOR": "1",
    }
    args = ["bash", str(SCRIPT), "--skip", "cleanup,litellm,openwebui"]
    if skip:
        args[-1] += ",codex"
    result = subprocess.run(
        args, cwd=ROOT, env=env, capture_output=True, text=True, timeout=40
    )
    return result, config


def test_empty_host_without_poetry_or_codex_is_core_eligible(tmp_path):
    result, config = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "Codex CLI absent, no profile or explicit setup gate" in result.stdout
    assert not config.exists()


def test_installed_codex_requires_locked_poetry_before_mutation(tmp_path):
    result, config = _run(tmp_path, installed=True)
    assert result.returncode != 0
    assert "Codex configuration requires Poetry" in result.stderr
    assert not config.exists()


def test_existing_user_profile_requires_poetry_and_is_preserved(tmp_path):
    result, config = _run(tmp_path, existing=True)
    assert result.returncode != 0
    assert "Codex configuration requires Poetry" in result.stderr
    assert config.read_text() == "# user-owned profile\n"


def test_explicit_gateway_setup_requires_poetry_even_without_cli(tmp_path):
    result, config = _run(tmp_path, requested=True)
    assert result.returncode != 0
    assert "Codex configuration requires Poetry" in result.stderr
    assert not config.exists()


def test_explicit_skip_preserves_profile_without_poetry(tmp_path):
    result, config = _run(tmp_path, existing=True, skip=True)
    assert result.returncode == 0, result.stderr
    assert "Skipping Codex configuration (--skip codex)" in result.stdout
    assert config.read_text() == "# user-owned profile\n"
