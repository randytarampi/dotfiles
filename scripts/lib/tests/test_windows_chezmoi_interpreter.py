"""Windows Git Bash chezmoi interpreter producer and CI consumer checks."""

import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "scripts/bootstrap/windows-chezmoi-interpreter.sh"
WORKFLOW = ROOT / ".github/workflows/ci.yml"
NATIVE_BASH = r"C:\Program Files\Git\usr\bin\bash.exe"


def _invoke(tmp_path, *args, native_bash=NATIVE_BASH):
    stubs = tmp_path / "bin"
    stubs.mkdir(exist_ok=True)
    stub = stubs / "cygpath"
    stub.write_text('#!/bin/sh\nprintf "%s\\n" "$FAKE_NATIVE_BASH"\n')
    stub.chmod(0o755)
    env = {
        "HOME": str(tmp_path),
        "PATH": f"{stubs}:/usr/bin:/bin",
        "FAKE_NATIVE_BASH": native_bash,
    }
    return subprocess.run(
        ["bash", str(HELPER), *map(str, args)],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_preview_is_read_only_and_preserves_windows_backslashes(tmp_path):
    result = _invoke(tmp_path, "--print")
    assert result.returncode == 0, result.stderr
    expected = f"[interpreters.sh]\n    command = '{NATIVE_BASH}'\n"
    assert result.stdout == expected
    assert not (tmp_path / "chezmoi.toml").exists()
    assert list(tmp_path.glob(".chezmoi-interpreter.*")) == []


def test_explicit_write_then_check_roundtrip(tmp_path):
    destination = tmp_path / "chezmoi.toml"
    written = _invoke(tmp_path, "--write", destination)
    assert written.returncode == 0, written.stderr
    assert destination.read_text() == _invoke(tmp_path, "--print").stdout
    assert _invoke(tmp_path, "--check", destination).returncode == 0
    if os.name != "nt":
        assert destination.stat().st_mode & 0o077 == 0


def test_existing_file_and_symlink_are_never_silently_overwritten(tmp_path):
    destination = tmp_path / "chezmoi.toml"
    destination.write_text("user-owned\n")
    refusal = _invoke(tmp_path, "--write", destination)
    assert refusal.returncode != 0
    assert destination.read_text() == "user-owned\n"
    changed = _invoke(tmp_path, "--write", destination, "--force")
    assert changed.returncode == 0, changed.stderr
    assert destination.read_text() != "user-owned\n"

    target = tmp_path / "foreign"
    target.write_text("preserve me\n")
    destination.unlink()
    destination.symlink_to(target)
    refusal = _invoke(tmp_path, "--write", destination, "--force")
    assert refusal.returncode != 0
    assert target.read_text() == "preserve me\n"


@pytest.mark.parametrize(
    "bad_path",
    [
        "not-a-windows-executable",
        "relative.exe",
        r"C:relative\bash.exe",
        "C:\\x'evil.exe",
    ],
)
def test_rejects_non_executable_or_quote_unsafe_paths(tmp_path, bad_path):
    result = _invoke(tmp_path, "--print", native_bash=bad_path)
    assert result.returncode != 0
    assert not result.stdout


def test_write_rejects_directory_and_fifo_even_with_force(tmp_path):
    directory = tmp_path / "existing-directory"
    directory.mkdir()
    result = _invoke(tmp_path, "--write", directory, "--force")
    assert result.returncode != 0
    assert list(directory.iterdir()) == []
    assert list(tmp_path.glob(".chezmoi-interpreter.*")) == []

    # A FIFO is an existing, non-regular destination and cannot be adopted.
    fifo = tmp_path / "existing-fifo"
    os.mkfifo(fifo)
    result = _invoke(tmp_path, "--write", fifo, "--force")
    assert result.returncode != 0
    assert fifo.is_fifo()
    assert list(tmp_path.glob(".chezmoi-interpreter.*")) == []


def test_write_rejects_dangling_symlink_even_with_force(tmp_path):
    target = tmp_path / "missing-user-owned"
    destination = tmp_path / "dangling-link"
    destination.symlink_to(target)
    result = _invoke(tmp_path, "--write", destination, "--force")
    assert result.returncode != 0
    assert destination.is_symlink()
    assert not target.exists()


def test_check_detects_modified_user_config(tmp_path):
    destination = tmp_path / "chezmoi.toml"
    destination.write_text("user-owned\n")
    result = _invoke(tmp_path, "--check", destination)
    assert result.returncode != 0
    assert destination.read_text() == "user-owned\n"


def test_ci_reuses_the_helper_and_keeps_existing_windows_deploy_required():
    workflow = WORKFLOW.read_text()
    deploy = workflow.split("  deploy:", 1)[1].split("  bootstrap-smoke:", 1)[0]
    assert "windows-latest" in deploy
    assert "bash scripts/bootstrap/windows-chezmoi-interpreter.sh --write" in deploy
    assert "bash scripts/bootstrap/windows-chezmoi-interpreter.sh --check" in deploy
    assert 'echo "CHEZMOI=chezmoi --config $cz_config" >> "$GITHUB_ENV"' in deploy
    assert "- name: Deploy (first pass)" in deploy
    assert "- name: Deploy (idempotency pass)" in deploy
    assert "- name: Chezmoi no-op check" in deploy
