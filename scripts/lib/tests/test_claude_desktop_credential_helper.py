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


SPEC = importlib.util.spec_from_file_location(
    "claude_desktop_credential_helper", SCRIPT
)
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


def test_secret_free_wrapper_runs_with_restricted_gui_path(tmp_path):
    home = tmp_path / "home"
    key = home / ".local/share/litellm/clients/claude-desktop.key"
    key.parent.mkdir(parents=True)
    key.write_text("sk-synthetic-key", encoding="ascii")
    key.chmod(0o600)
    wrapper_path = tmp_path / "Claude Desktop wrapper.sh"
    generated = subprocess.run(
        [sys.executable, str(SCRIPT), "--wrapper"],
        env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert generated.returncode == 0
    assert generated.stderr == ""
    assert generated.stdout.startswith("#!/bin/sh\\nexec ")
    assert "sk-synthetic-key" not in generated.stdout
    assert str(Path(sys.executable).resolve()) in generated.stdout
    assert str(SCRIPT.resolve()) in generated.stdout
    wrapper_path.write_text(generated.stdout, encoding="utf-8")
    wrapper_path.chmod(0o700)
    checked = subprocess.run(
        [str(wrapper_path), "--check"],
        env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert checked.returncode == 0, checked.stderr
    assert checked.stdout == "Claude Desktop credential ready\\n"
    assert "sk-synthetic-key" not in checked.stdout + checked.stderr


@pytest.mark.parametrize("subject", ["directory", "file"])
@pytest.mark.parametrize("check", [False, True])
def test_wrong_owner_fails_without_secret_output(tmp_path, monkeypatch, subject, check):
    _, key = invoke(tmp_path)
    target = key.parent if subject == "directory" else key
    original_lstat = HELPER.Path.lstat
    original_uid = HELPER.os.geteuid()

    def wrong_owner_lstat(path, *args, **kwargs):
        metadata = original_lstat(path, *args, **kwargs)
        if path != target:
            return metadata
        parts = list(metadata)
        parts[4] = original_uid + 1
        return os.stat_result(parts)

    stdout = io.StringIO()
    stderr = io.StringIO()
    with monkeypatch.context() as patch:
        patch.setattr(HELPER.Path, "home", lambda: tmp_path / "home")
        patch.setattr(HELPER.Path, "lstat", wrong_owner_lstat)
        patch.setattr(HELPER.sys, "stdout", stdout)
        patch.setattr(HELPER.sys, "stderr", stderr)
        assert HELPER.main(["--check"] if check else []) == 1
    assert stdout.getvalue() == ""
    assert "credential unavailable or unsafe" in stderr.getvalue()
    assert "sk-synthetic-key" not in stderr.getvalue()
