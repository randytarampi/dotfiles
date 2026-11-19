"""Behavioural tests for `make plannotator-restart`.

The previous recipe ran `lsof -ti:19433 | xargs kill -9` unconditionally and
then printed "Plannotator restarted." whatever happened. That could kill a
foreign process on the port and could never report failure.

The replacement identifies the managed LaunchAgent, stops it through the
service manager, waits for the port to free, and refuses to kill a process
the service manager does not own.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _write_stub(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _env(tmp_path: Path, *, launcher_ok: bool, port_busy: bool) -> dict:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _write_stub(bin_dir / "uname", 'echo "Darwin"\n')
    _write_stub(bin_dir / "launchctl", "exit 0\n" if launcher_ok else "exit 1\n")
    _write_stub(bin_dir / "systemctl", "exit 1\n")
    # A stub lsof that either reports a listening pid or nothing.
    _write_stub(
        bin_dir / "lsof",
        'printf "COMMAND PID\\nrogue 4242\\n"\n' if port_busy else "exit 1\n",
    )
    env = dict(os.environ)
    env["HOME"] = str(tmp_path)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    return env


def _run(tmp_path: Path, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["make", "-s", "--no-print-directory", "plannotator-restart"],
        cwd=REPO_ROOT,
        env=_env(tmp_path, **kwargs),
        capture_output=True,
        text=True,
        check=False,
    )


def test_absent_launchagent_is_a_skip(tmp_path: Path):
    result = _run(tmp_path, launcher_ok=True, port_busy=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SKIPPED" in result.stdout
    assert "restarted" not in result.stdout.lower()


def test_foreign_listener_is_refused_not_killed(tmp_path: Path):
    plist = tmp_path / "Library" / "LaunchAgents" / "com.plannotator.paste.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text("<plist/>", encoding="utf-8")
    result = _run(tmp_path, launcher_ok=False, port_busy=True)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "refusing to kill" in result.stderr
    assert "FAILED" in result.stderr


def test_managed_restart_succeeds_when_port_frees(tmp_path: Path):
    plist = tmp_path / "Library" / "LaunchAgents" / "com.plannotator.paste.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text("<plist/>", encoding="utf-8")
    result = _run(tmp_path, launcher_ok=True, port_busy=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "APPLIED" in result.stdout


def test_no_unconditional_success_claim_remains():
    text = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    assert "Plannotator restarted." not in text
    assert (
        "kill -9"
        not in text.split("plannotator-restart:")[1].split("services-restart:")[0]
    )
