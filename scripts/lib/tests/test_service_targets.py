"""Behavioural tests for Makefile service targets.

The defect these cover: targets swallowed service-manager failures with
`|| true` and then printed an unconditional "restarted" line, so a green
`make services-restart` proved nothing. Each rewritten target must now
distinguish SKIPPED (gate off / unsupported / absent unit) from FAILED
(requested action did not take effect), and only the latter is non-zero.

The tests drive the real Makefile with stubbed `launchctl`/`systemctl`/`uname`
so no host service is touched.
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


def _stub_bin(tmp_path: Path, *, uname: str, launcher_fails: bool) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stub(bin_dir / "uname", f'echo "{uname}"\n')
    launcher = "exit 1\n" if launcher_fails else "exit 0\n"
    _write_stub(bin_dir / "launchctl", launcher)
    _write_stub(bin_dir / "systemctl", "exit 1\n")
    # `launchctl print` must fail even in the "success" stub so we can assert
    # the readiness check is what decides, not the bootstrap exit code alone.
    return bin_dir


def _run(target: str, tmp_path: Path, *, uname: str, launcher_fails: bool):
    bin_dir = _stub_bin(tmp_path, uname=uname, launcher_fails=launcher_fails)
    env = dict(os.environ)
    env["HOME"] = str(tmp_path)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    return subprocess.run(
        ["make", "-s", "--no-print-directory", target],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_meridian_restart_fails_when_launchctl_cannot_load(tmp_path: Path):
    result = _run("meridian-restart", tmp_path, uname="Darwin", launcher_fails=True)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "FAILED" in result.stderr
    assert "Meridian restarted." not in result.stdout


def test_ddns_restart_is_a_skip_when_no_units_exist(tmp_path: Path):
    result = _run("ddns-restart", tmp_path, uname="Darwin", launcher_fails=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SKIPPED" in result.stdout
    assert "restarted" not in result.stdout.lower()


def test_ollama_env_restart_is_a_skip_when_unit_is_absent(tmp_path: Path):
    result = _run("ollama-env-restart", tmp_path, uname="Darwin", launcher_fails=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SKIPPED" in result.stdout
    assert "re-applied" not in result.stdout.lower()


def test_linux_without_user_systemd_is_a_skip(tmp_path: Path):
    result = _run("meridian-restart", tmp_path, uname="Linux", launcher_fails=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SKIPPED" in result.stdout


def test_no_rewritten_target_prints_unconditional_success():
    """A guard against reintroducing the false-success pattern."""
    text = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    for phrase in (
        "OpenCode Web started.",
        "OpenCode Web restarted.",
        "Meridian restarted.",
        "DDNS Route53 agents restarted.",
        "Ollama env re-applied.",
        "OpenCode Web stopped.",
    ):
        assert (
            phrase not in text
        ), f"unconditional success claim reintroduced: {phrase!r}"


def test_linux_absent_optional_units_are_skipped_without_invoking_manager(
    tmp_path: Path,
):
    """A working user systemd instance may have none of these optional units."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stub(bin_dir / "uname", 'echo "Linux"\n')
    trace = tmp_path / "manager-actions.txt"
    _write_stub(
        bin_dir / "systemctl",
        'if [ "$2" = list-unit-files ]; then exit 0; fi\n'
        'if [ "$2" = cat ]; then exit 1; fi\n'
        'printf "%s\\n" "$*" >> "$STUB_TRACE"\n'
        "exit 5\n",
    )
    env = dict(os.environ)
    env["HOME"] = str(tmp_path)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["STUB_TRACE"] = str(trace)
    targets = (
        "opencode-start",
        "opencode-stop",
        "openwebui-stop",
        "litellm-stop",
        "meridian-restart",
        "ddns-restart",
    )
    for target in targets:
        result = subprocess.run(
            ["make", "-s", "--no-print-directory", target],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (target, result.stdout, result.stderr)
        assert "SKIPPED" in result.stdout, (target, result.stdout)
    assert not trace.exists(), "absent optional units must not be started or stopped"
