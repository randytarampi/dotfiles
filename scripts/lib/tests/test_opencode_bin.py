"""Managed OpenCode binary resolver precedence regressions.

Mirrors scripts/lib/opencode_bin.sh (shell) and opencode_host.host_binary
(python): explicit OPENCODE_MANAGED_BIN > $HOME/.opencode/bin/<name> > PATH,
so a managed v2 binary is never shadowed by a stale PATH v1 in non-login
consumers (chezmoi children, launchd jobs, doctor runs).
"""

import stat
from pathlib import Path

import opencode_host

ROOT = Path(__file__).resolve().parents[3]
LIB = ROOT / "scripts/lib"


def _make_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def _run_resolver(tmp_path, monkeypatch, managed=None, env=None, path=None):
    fake_bin = tmp_path / "fakepath"
    fake_bin.mkdir(exist_ok=True)
    fake_opencode = _make_executable(fake_bin / "opencode")
    fake_opencode.write_text(
        '#!/bin/sh\ncase "$1" in --version) echo "opencode v2.9.9";; esac\n'
    )
    fake_opencode.chmod(fake_opencode.stat().st_mode | stat.S_IEXEC)
    if managed is not None:
        _make_executable(tmp_path / "home/.opencode/bin/opencode").write_text(
            "#!/bin/sh\nprintf managed\n"
        )
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PATH", str(fake_bin))
    monkeypatch.delenv("OPENCODE_MANAGED_BIN", raising=False)
    for key, value in (env or {}).items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    script = LIB / "opencode_bin.sh"
    proc = __import__("subprocess").run(
        [
            "/bin/bash",
            "-c",
            f'set -u; source "{script}"; opencode_managed_bin',
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    python_value = opencode_host.host_binary()
    return proc.stdout.strip(), python_value, fake_opencode


def test_resolver_prefers_managed_over_path(tmp_path, monkeypatch):
    shell_value, python_value, _fake = _run_resolver(
        tmp_path, monkeypatch, managed=True
    )
    assert shell_value.endswith("/.opencode/bin/opencode")
    assert python_value == shell_value


def test_resolver_explicit_env_wins(tmp_path, monkeypatch):
    override = str(tmp_path / "custom/opencode")
    (_make_executable(tmp_path / "custom/opencode")).write_text("#!/bin/sh\n")
    shell_value, python_value, _fake = _run_resolver(
        tmp_path, monkeypatch, managed=True, env={"OPENCODE_MANAGED_BIN": override}
    )
    assert shell_value == override
    assert python_value == override


def test_resolver_falls_back_to_path(tmp_path, monkeypatch):
    shell_value, python_value, fake = _run_resolver(tmp_path, monkeypatch, managed=None)
    assert shell_value == str(fake)
    assert python_value == str(fake)


def test_resolver_empty_when_nothing_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.delenv("OPENCODE_MANAGED_BIN", raising=False)
    (tmp_path / "empty").mkdir()
    import subprocess

    proc = subprocess.run(
        [
            "/bin/bash",
            "-c",
            f'set -u; source "{LIB / "opencode_bin.sh"}"; opencode_managed_bin',
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""
    assert opencode_host.host_binary() is None


def test_windows_nsis_candidates_are_resolved(monkeypatch, tmp_path):
    """On Windows the verified NSIS channel's app dirs are the managed
    binary home; resolution binds the CLI across processes."""
    nsis = tmp_path / "LocalAppData" / "Programs" / "opencode"
    nsis.mkdir(parents=True)
    exe = nsis / "opencode.exe"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    import platform as platform_module

    monkeypatch.setattr(platform_module, "system", lambda: "Windows")
    monkeypatch.setattr(opencode_host.platform, "system", lambda: "Windows")
    monkeypatch.setattr(opencode_host.shutil, "which", lambda _name: None)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    monkeypatch.delenv("OPENCODE_MANAGED_BIN", raising=False)
    assert opencode_host.host_binary() == str(exe)
