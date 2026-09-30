import os
import subprocess
import sys
from pathlib import Path

import pytest

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows test path
    fcntl = None

import generation_lock


def test_help_does_not_create_lock_file(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(sys, "argv", ["configure", "--help"])
    global SCRIPT_DIR
    SCRIPT_DIR = str(tmp_path / "source/scripts")

    @generation_lock.with_generation_lock("ignored")
    def run():
        return 0

    assert run() == 0
    assert not (tmp_path / ".cache/dotfiles/generation.lock").exists()


def test_dry_run_does_not_create_lock_file(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(sys, "argv", ["configure", "--dry-run"])
    global SCRIPT_DIR
    SCRIPT_DIR = str(tmp_path / "source/scripts")

    @generation_lock.with_generation_lock("ignored")
    def run():
        return 0

    assert run() == 0
    assert not (tmp_path / ".cache/dotfiles/generation.lock").exists()


def test_contention_returns_nonzero(tmp_path, monkeypatch):
    if fcntl is None:
        pytest.skip("fcntl unavailable; Windows locking is covered separately")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(sys, "argv", ["configure"])
    global SCRIPT_DIR
    SCRIPT_DIR = str(tmp_path / "source")
    lock_path = tmp_path / ".cache/dotfiles/generation.lock"
    lock_path.parent.mkdir(parents=True)
    with lock_path.open("a+") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        @generation_lock.with_generation_lock("ignored")
        def run():
            return 0

        assert run() == 1


def test_subprocess_contention_exits_nonzero(tmp_path, monkeypatch):
    if fcntl is None:
        pytest.skip("fcntl unavailable; Windows locking is covered separately")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    lock_path = tmp_path / ".cache/dotfiles/generation.lock"
    lock_path.parent.mkdir(parents=True)
    code = """
import sys
from pathlib import Path
sys.path.insert(0, 'scripts/lib')
import generation_lock
SCRIPT_DIR = str(Path.cwd() / 'scripts')
@generation_lock.with_generation_lock('ignored')
def main():
    return 0
raise SystemExit(main())
"""
    with lock_path.open("a+") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path.cwd(),
            env={**os.environ, "HOME": str(tmp_path), "PYTHONPATH": "scripts/lib"},
            capture_output=True,
            text=True,
            check=False,
        )
    assert result.returncode != 0


def test_msvcrt_branch_restores_offset(monkeypatch, tmp_path):
    calls = []

    class FakeMsvcrt:
        LK_NBLCK = 1
        LK_UNLCK = 2

        @staticmethod
        def locking(fd, mode, size):
            calls.append((mode, os.lseek(fd, 0, os.SEEK_CUR), size))

    monkeypatch.setattr(generation_lock, "_LOCK_BACKEND", "msvcrt")
    monkeypatch.setattr(generation_lock, "_msvcrt", FakeMsvcrt, raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(sys, "argv", ["configure"])
    global SCRIPT_DIR
    SCRIPT_DIR = str(tmp_path / "source/scripts")

    @generation_lock.with_generation_lock("ignored")
    def run():
        return 0

    assert run() == 0
    assert [call[0] for call in calls] == [FakeMsvcrt.LK_NBLCK, FakeMsvcrt.LK_UNLCK]
    assert calls[0][1] == calls[1][1] == 0


def test_cross_source_denied_same_source_allowed(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(sys, "argv", ["configure"])
    global SCRIPT_DIR
    SCRIPT_DIR = str(tmp_path / "source-a/scripts")

    @generation_lock.with_generation_lock("ignored")
    def run():
        return 0

    assert run() == 0
    SCRIPT_DIR = str(tmp_path / "source-b/scripts")
    assert run() == 1
    SCRIPT_DIR = str(tmp_path / "source-a/scripts")
    assert run() == 0
