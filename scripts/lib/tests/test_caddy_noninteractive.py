"""The Caddy deploy step must not block an unattended chezmoi apply."""

import os
import pty
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = ROOT / ".chezmoiscripts/run_onchange_24-install-caddy.sh.tmpl"


def _fake_environment(tmp_path, gate):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env").write_text(f"DOTFILES_RUN_CADDY_SETUP='{gate}'\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sudo = bin_dir / "sudo"
    sudo.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$@" >> "$SUDO_CALLS"\n'
        '[ "${SUDO_SUCCEEDS:-0}" = "1" ] && exit 0\nexit 1\n'
    )
    sudo.chmod(0o755)
    fake_id = bin_dir / "id"
    fake_id.write_text('#!/bin/sh\n[ "${1:-}" = "-u" ] || exit 1\nprintf "501\\n"\n')
    fake_id.chmod(0o755)
    calls = tmp_path / "sudo-calls"
    service_calls = tmp_path / "service-calls"
    for command in ("launchctl", "systemctl", "pkill"):
        stub = bin_dir / command
        stub.write_text(
            f'#!/bin/sh\nprintf "{command} %s\\n" "$*" >> "$SERVICE_CALLS"\nexit 0\n'
        )
        stub.chmod(0o755)
    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "BREW_BIN": "/usr/bin/true",
        "SUDO_CALLS": str(calls),
        "SERVICE_CALLS": str(service_calls),
    }
    env.pop("CADDY_EXPLICIT_RETRY", None)
    return env, calls


def _render(env):
    return subprocess.run(
        ["chezmoi", "--source", str(ROOT), "execute-template"],
        input=TEMPLATE.read_text(),
        text=True,
        capture_output=True,
        env=env,
        check=True,
    ).stdout


def _run_with_unavailable_sudo(tmp_path, gate):
    env, calls = _fake_environment(tmp_path, gate)
    result = subprocess.run(
        ["bash"], input=_render(env), text=True, capture_output=True, env=env
    )
    return result, calls.read_text() if calls.exists() else ""


def test_noninteractive_caddy_skips_when_sudo_is_unavailable(tmp_path):
    result, calls = _run_with_unavailable_sudo(tmp_path, "1")
    assert result.returncode == 0, result.stderr
    assert "sudo" in result.stderr.lower() and "skipping" in result.stderr.lower()
    assert calls.splitlines() == ["-n", "true"]
    assert not (tmp_path / "service-calls").exists()


def test_disabled_caddy_never_asks_for_sudo(tmp_path):
    result, calls = _run_with_unavailable_sudo(tmp_path, "0")
    assert result.returncode == 0, result.stderr
    assert calls == ""


def test_targeted_caddy_retry_fails_when_noninteractive_sudo_is_unavailable(tmp_path):
    env, calls = _fake_environment(tmp_path, "1")
    for _ in range(2):
        result = subprocess.run(
            ["make", "-C", str(ROOT), "caddy-setup"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 2, result.stderr
        assert "skipping" in result.stderr.lower()
    assert calls.read_text().splitlines() == ["-n", "true", "-n", "true"]
    assert not Path(env["SERVICE_CALLS"]).exists()


def test_targeted_caddy_retry_succeeds_when_noninteractive_sudo_is_authorized(
    tmp_path,
):
    env, calls = _fake_environment(tmp_path, "1")
    env["SUDO_SUCCEEDS"] = "1"
    for _ in range(2):
        result = subprocess.run(
            ["make", "-C", str(ROOT), "caddy-setup"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
    lines = calls.read_text().splitlines()
    assert len(lines) == 10
    for index in (0, 5):
        assert lines[index : index + 3] == ["-n", "true", "-n"]
        assert lines[index + 3].startswith("--preserve-env=")
        assert "CADDY_EXPLICIT_RETRY" in lines[index + 3]
    assert not Path(env["SERVICE_CALLS"]).exists()


def test_caddy_retry_gate_and_dry_run_are_side_effect_free(tmp_path):
    env, calls = _fake_environment(tmp_path, "0")
    disabled = subprocess.run(
        ["make", "-C", str(ROOT), "caddy-setup"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert disabled.returncode == 0
    assert "gate is off" in disabled.stdout
    assert not calls.exists()
    assert not Path(env["SERVICE_CALLS"]).exists()

    (Path(env["HOME"]) / ".env").write_text("DOTFILES_RUN_CADDY_SETUP='1'\n")
    dry_run_dir = tmp_path / "dry-run-temp"
    dry_run_dir.mkdir()
    env["TMPDIR"] = str(dry_run_dir)
    preview = subprocess.run(
        ["make", "-C", str(ROOT), "caddy-setup", "DRY_RUN=1"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert preview.returncode == 0
    assert "Would render and run" in preview.stdout
    assert not calls.exists()
    assert not Path(env["SERVICE_CALLS"]).exists()
    assert list(dry_run_dir.iterdir()) == []


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS sudo re-exec path")
def test_interactive_caddy_keeps_prompting_sudo_path(tmp_path):
    env, calls = _fake_environment(tmp_path, "1")
    master, slave = pty.openpty()
    try:
        result = subprocess.run(
            ["bash"],
            input=_render(env),
            text=True,
            stdout=subprocess.PIPE,
            stderr=slave,
            env=env,
            timeout=10,
        )
    finally:
        os.close(slave)
        os.close(master)
    assert result.returncode == 1  # the fake sudo declines, not the preflight
    assert calls.read_text().splitlines()[0].startswith("--preserve-env=")
