"""Behavioural tests for the Caddy Make targets.

Issue #323 was filed because the Caddy installer printed "Caddy setup
complete" with exit 0 when required operations failed. #326 fixed the
installer and left the Make targets, which had the same shape:

  caddy-validate:  `caddy validate ... || echo "failed"`
  caddy-reload:    `caddy reload   ... || echo "reload failed"`
  caddy-restart:   `... || true` twice, then `echo "Caddy restarted."`

All three now report SKIPPED / APPLIED / FAILED and exit non-zero on a real
failure. These tests drive the real Makefile with stubbed binaries so no
privileged operation runs and nothing depends on the developer's machine.

The macOS `caddy-restart` path inspects an absolute LaunchDaemon path that
cannot be stubbed, so the skip/failure assertions use the Linux branch.
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


def _bin(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    return bin_dir


def _env(tmp_path: Path, *, uname: str, caddy_ok: bool, units: str = "") -> dict:
    bin_dir = _bin(tmp_path)
    _write_stub(bin_dir / "uname", f'echo "{uname}"\n')
    _write_stub(bin_dir / "brew", f'echo "{tmp_path}"\n')
    _write_stub(bin_dir / "sudo", 'exec "$@"\n')
    _write_stub(bin_dir / "sleep", "exit 0\n")
    _write_stub(bin_dir / "launchctl", "exit 0\n" if caddy_ok else "exit 1\n")
    restart_rc = 0 if caddy_ok else 1
    _write_stub(
        bin_dir / "systemctl",
        'case "$1" in\n'
        '  list-unit-files) [ -n "'
        + units
        + "\" ] && printf '%s\\n' \""
        + units
        + '" ;;\n'
        f"  restart) exit {restart_rc} ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n",
    )
    _write_stub(bin_dir / "caddy", "exit 0\n" if caddy_ok else "exit 1\n")
    env = dict(os.environ)
    env["HOME"] = str(tmp_path)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    return env


def _run(target: str, tmp_path: Path, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["make", "-s", "--no-print-directory", target],
        cwd=REPO_ROOT,
        env=_env(tmp_path, **kw),
        capture_output=True,
        text=True,
        check=False,
    )


def _caddyfile(tmp_path: Path) -> None:
    cfg = tmp_path / "etc" / "caddy" / "Caddyfile"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("{}\n", encoding="utf-8")


def test_explicit_validate_fails_without_a_managed_caddyfile(tmp_path: Path):
    result = _run("caddy-validate", tmp_path, uname="Darwin", caddy_ok=True)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "FAILED" in result.stderr


def test_validate_fails_on_invalid_config(tmp_path: Path):
    _caddyfile(tmp_path)
    result = _run("caddy-validate", tmp_path, uname="Darwin", caddy_ok=False)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "FAILED" in result.stderr


def test_validate_succeeds_on_valid_config(tmp_path: Path):
    _caddyfile(tmp_path)
    result = _run("caddy-validate", tmp_path, uname="Darwin", caddy_ok=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "APPLIED" in result.stdout


def test_reload_fails_when_reload_fails(tmp_path: Path):
    _caddyfile(tmp_path)
    result = _run("caddy-reload", tmp_path, uname="Darwin", caddy_ok=False)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "FAILED" in result.stderr


def test_restart_skips_when_no_caddy_unit_on_linux(tmp_path: Path):
    result = _run("caddy-restart", tmp_path, uname="Linux", caddy_ok=True, units="")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SKIPPED" in result.stdout
    assert "Caddy restarted." not in result.stdout


def test_restart_fails_when_unit_restart_fails_on_linux(tmp_path: Path):
    result = _run(
        "caddy-restart",
        tmp_path,
        uname="Linux",
        caddy_ok=False,
        units="caddy.service enabled",
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "FAILED" in result.stderr


def test_no_swallowed_failure_or_unconditional_success():
    text = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    assert "Caddy restarted." not in text, "unconditional success claim reintroduced"
    # The old shape reported failure and still exited 0.
    assert '|| echo "Caddy reload failed"' not in text
    assert '|| echo "Caddyfile validation failed' not in text


def test_explicit_reload_fails_without_a_managed_caddyfile(tmp_path: Path):
    result = _run("caddy-reload", tmp_path, uname="Darwin", caddy_ok=True)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "FAILED" in result.stderr


def test_explicit_validate_missing_binary_is_failure_not_success(tmp_path: Path):
    _caddyfile(tmp_path)
    result = _run("caddy-validate", tmp_path, uname="Darwin", caddy_ok=False)
    assert result.returncode != 0
