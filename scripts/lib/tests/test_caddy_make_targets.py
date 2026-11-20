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


# --- #355: unprivileged validation must not be labelled a syntax failure ---

PKI_ERROR = (
    '{"level":"error","msg":"loading tls app module: provision pki: '
    "loading intermediate cert: open "
    "/Users/x/Library/Application Support/Caddy/pki/authorities/local/"
    'intermediate.crt: permission denied"}'
)


def _run_with_caddy(tmp_path, caddy_body, *, uname="Darwin", adapt_rc=0):
    """Run validate with a fake provisioner and independent adapter result."""
    env = _env(tmp_path, uname=uname, caddy_ok=True)
    _write_stub(
        tmp_path / "bin" / "caddy",
        f'if [ "$1" = adapt ]; then exit {adapt_rc}; fi\n' + caddy_body,
    )
    return subprocess.run(
        ["make", "-s", "--no-print-directory", "caddy-validate"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _caddyfile_for(tmp_path):
    cfg = tmp_path / "etc" / "caddy" / "Caddyfile"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("{}\n", encoding="utf-8")


def test_root_pki_unreadable_is_unknown_not_failed(tmp_path):
    _caddyfile_for(tmp_path)
    result = _run_with_caddy(tmp_path, f'printf "%s\\n" {PKI_ERROR!r} >&2\nexit 1\n')
    assert result.returncode != 0, "UNKNOWN must still be a non-zero exit"
    assert "UNKNOWN" in result.stderr
    assert "FAILED" not in result.stderr
    assert "#355" in result.stderr


def test_unrelated_permission_error_stays_failed(tmp_path):
    _caddyfile_for(tmp_path)
    result = _run_with_caddy(
        tmp_path,
        'printf "%s\\n" "open /etc/other.conf: permission denied" >&2\nexit 1\n',
    )
    assert result.returncode != 0
    assert "FAILED" in result.stderr
    assert "UNKNOWN" not in result.stderr


def test_valid_config_reports_applied(tmp_path):
    _caddyfile_for(tmp_path)
    result = _run_with_caddy(tmp_path, "exit 0\n")
    assert result.returncode == 0
    assert "APPLIED" in result.stdout


def test_missing_caddyfile_message_names_the_real_path(tmp_path):
    """Regression: a single $ in the recipe let make expand $c, printing 'onfig'."""
    env = _env(tmp_path, uname="Darwin", caddy_ok=True)
    result = subprocess.run(
        ["make", "-s", "--no-print-directory", "caddy-validate"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "onfig" not in result.stderr, "make-expanded $c: path was mangled"
    assert "Caddyfile" in result.stderr


def test_pki_match_does_not_mask_invalid_caddyfile(tmp_path):
    """Even a PKI-looking error cannot override a failed parse/adaptation."""
    _caddyfile_for(tmp_path)
    result = _run_with_caddy(
        tmp_path,
        f'printf "%s\\n" {PKI_ERROR!r} >&2\nexit 1\n',
        adapt_rc=1,
    )
    assert result.returncode != 0
    assert "FAILED" in result.stderr
    assert "UNKNOWN" not in result.stderr
    assert "loading intermediate cert" in result.stderr


def test_validate_preserves_original_caddy_failure_diagnostic(tmp_path):
    _caddyfile_for(tmp_path)
    result = _run_with_caddy(
        tmp_path,
        'printf "%s\\n" "caddyfile:12: unrecognized directive" >&2\nexit 1\n',
    )
    assert result.returncode != 0
    assert "FAILED" in result.stderr
    assert "caddyfile:12: unrecognized directive" in result.stderr


def test_validate_refuses_failed_brew_prefix_even_with_config_file(tmp_path):
    _caddyfile_for(tmp_path)
    env = _env(tmp_path, uname="Darwin", caddy_ok=True)
    _write_stub(tmp_path / "bin" / "brew", f'echo "{tmp_path}"\nexit 1\n')
    result = subprocess.run(
        ["make", "-s", "--no-print-directory", "caddy-validate"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "brew --prefix failed" in result.stderr
    assert "APPLIED" not in result.stdout


def test_reload_refuses_failed_brew_prefix_before_privileged_command(tmp_path):
    _caddyfile_for(tmp_path)
    env = _env(tmp_path, uname="Darwin", caddy_ok=True)
    _write_stub(tmp_path / "bin" / "brew", f'echo "{tmp_path}"\nexit 1\n')
    _write_stub(tmp_path / "bin" / "sudo", 'printf "%s\\n" "sudo called" >&2\nexit 0\n')
    result = subprocess.run(
        ["make", "-s", "--no-print-directory", "caddy-reload"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "brew --prefix failed" in result.stderr
    assert "sudo called" not in result.stderr
