"""Read-only Claude Desktop credential-helper fixture coverage."""

import importlib.util
import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "claude-desktop-credential-helper.py"


def invoke(tmp_path, secret="sk-synthetic-key", mode=0o600, check=False):
    home = tmp_path / "home"
    parent = home / ".local/share/litellm/clients"
    parent.mkdir(parents=True, exist_ok=True)
    key = parent / "claude-desktop.key"
    if secret is not None:
        key.write_bytes(secret.encode("utf-8"))
        key.chmod(mode)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *(["--check"] if check else [])],
        env={"HOME": str(home), "PATH": os.defpath},
        text=True,
        capture_output=True,
        check=False,
    )
    return result, key


def test_helper_stdout_and_no_mutation(tmp_path):
    result, key = invoke(tmp_path)
    original = key.read_bytes()
    assert result.returncode == 0
    assert result.stdout == "sk-synthetic-key\n"
    assert result.stderr == ""
    assert key.read_bytes() == original


def test_check_does_not_disclose_key(tmp_path):
    result, _ = invoke(tmp_path, check=True)
    assert result.returncode == 0
    assert result.stdout == "Claude Desktop credential ready\n"
    assert "sk-synthetic" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("secret", "mode"),
    [
        (None, 0o600),
        ("", 0o600),
        ("sk-a\nsk-b", 0o600),
        ("sk-a\n\n", 0o600),
        ("sk-with space", 0o600),
        ("sk-a", 0o644),
        ("sk-a", 0o666),
        ("A" * 4097, 0o600),
    ],
)
def test_bad_key_fails_without_stdout(tmp_path, secret, mode):
    result, _ = invoke(tmp_path, secret, mode)
    assert result.returncode == 1
    assert result.stdout == ""
    assert "credential unavailable or unsafe" in result.stderr


def test_terminal_newline(tmp_path):
    result, _ = invoke(tmp_path, "sk-synthetic\n")
    assert result.returncode == 0
    assert result.stdout == "sk-synthetic\n"


def test_symlinked_key_refused(tmp_path):
    _, key = invoke(tmp_path)
    original = key.with_suffix(".saved")
    key.rename(original)
    key.symlink_to(original)
    result, _ = invoke_existing(tmp_path)
    assert result.returncode == 1
    assert not result.stdout


def invoke_existing(tmp_path):
    home = tmp_path / "home"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        env={"HOME": str(home), "PATH": os.defpath},
        capture_output=True,
        text=True,
        check=False,
    )
    return result, home


def test_symlinked_parent_refused(tmp_path):
    _, key = invoke(tmp_path)
    parent = key.parent
    moved = parent.with_name("clients-saved")
    parent.rename(moved)
    parent.symlink_to(moved, target_is_directory=True)
    result, _ = invoke_existing(tmp_path)
    assert result.returncode == 1
    assert result.stdout == ""


def test_invalid_flag_exits_two(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--key-file", "/tmp/test"],
        env={"HOME": str(tmp_path), "PATH": os.defpath},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert result.stdout == ""

SPEC = importlib.util.spec_from_file_location("claude_desktop_credential_helper", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)


def test_in_process_success_and_no_secret_leak(tmp_path, monkeypatch):
    _, key = invoke(tmp_path)
    monkeypatch.setattr(HELPER.Path, "home", lambda: tmp_path / "home")
    assert HELPER.credential() == "sk-synthetic-key"
    output = io.StringIO()
    monkeypatch.setattr(HELPER.sys, "stdout", output)
    assert HELPER.main(["--check"]) == 0
    assert output.getvalue() == "Claude Desktop credential ready\n"
    assert key.read_bytes() == b"sk-synthetic-key"


@pytest.mark.parametrize(
    "failure",
    [
        "missing",
        "empty",
        "weak-file",
        "weak-directory",
        "oversize",
        "multiline",
        "non-ascii",
        "symlink-file",
        "symlink-directory",
        "directory-not-file",
    ],
)
def test_in_process_credential_failures(tmp_path, monkeypatch, failure):
    _, key = invoke(tmp_path)
    monkeypatch.setattr(HELPER.Path, "home", lambda: tmp_path / "home")
    if failure == "missing":
        key.unlink()
    elif failure == "empty":
        key.write_bytes(b"")
    elif failure == "weak-file":
        key.chmod(0o644)
    elif failure == "weak-directory":
        key.parent.chmod(0o777)
    elif failure == "oversize":
        key.write_bytes(b"A" * 4097)
    elif failure == "multiline":
        key.write_bytes(b"sk-one\nsk-two")
    elif failure == "non-ascii":
        key.write_bytes(b"sk-\xff")
    elif failure == "symlink-file":
        saved = key.with_suffix(".saved")
        key.rename(saved)
        key.symlink_to(saved)
    elif failure == "symlink-directory":
        parent = key.parent
        saved = parent.with_name("clients-saved")
        parent.rename(saved)
        parent.symlink_to(saved, target_is_directory=True)
    elif failure == "directory-not-file":
        key.unlink()
        key.mkdir()
    with pytest.raises((OSError, ValueError)):
        HELPER.credential()
