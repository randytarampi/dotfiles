"""Regression tests for the managed OpenCode v2 host installer."""

from pathlib import Path
import os
import subprocess

ROOT = Path(__file__).resolve().parents[3]
INSTALLER = ROOT / "scripts/install-opencode-v2.sh"


def run(
    tmp_path,
    *,
    pre_version=None,
    post_version="opencode 2.2.5",
    gate="1",
    install_rc=0,
    path_mode="stubbed",
    args=(),
):
    """install_rc="absent" omits the bash channel stub entirely.
    path_mode="isolated" leaves no system dirs on PATH (no curl, no real
    tools): every test stays network-free. The managed binary honours
    PROBE_RC for probe-failure cases."""
    home = tmp_path / "home"
    bindir = home / ".opencode/bin"
    bindir.mkdir(parents=True)
    stubdir = tmp_path / "bin"
    stubdir.mkdir()
    recorder = tmp_path / "calls"
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["DOTFILES_RUN_OPENCODE_TOOLS_SETUP"] = gate
    env["POST_VERSION"] = post_version
    if path_mode == "stubbed":
        env["PATH"] = f"{stubdir}:/usr/bin:/bin"
    else:
        env["PATH"] = str(stubdir)
    if pre_version is not None:
        # Pre-existing v1 (or v2) opencode earlier on PATH, shadowing nothing
        # once paths.sh's managed precedence applies.
        env["VERSION"] = pre_version
        fake = stubdir / "opencode"
        fake.write_text(
            'if [ "$1" = "--version" ]; then printf "%s\\n" "${VERSION}"; exit "${PROBE_RC:-0}"; fi\n'
            f'printf "%s\\n" "$*" >> "{recorder}"\n'
        )
        fake.chmod(0o755)
    if install_rc != "absent":
        # The installer's channel: the official script is invoked via
        # `curl ... | bash -s --`; the stub consumes stdin and writes the
        # freshly installed binary printing the post-install version.
        bash_stub = stubdir / "bash"
        bash_stub.write_text(
            f"printf install >> '{recorder}'\n"
            f"cat > '{bindir}/opencode' <<'SH'\n#!/bin/sh\n"
            'printf "%s\\n" "${POST_VERSION}"; exit "${PROBE_RC:-0}"\n'
            "SH\n"
            f"chmod +x '{bindir}/opencode'\n"
            "cat >/dev/null\n"
            f"exit {install_rc}\n"
        )
        bash_stub.chmod(0o755)
    result = subprocess.run(
        ["/bin/bash", str(INSTALLER), *args], env=env, capture_output=True, text=True
    )
    return result, recorder


def test_fresh_install_and_path_selection(tmp_path):
    result, recorder = run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "install" in recorder.read_text()
    assert (
        "opencode v2 verified" in result.stderr
        or "OpenCode v2 verified" in result.stderr
    )
    assert (tmp_path / "home/.opencode/bin/opencode").exists()


def test_v1_in_path_does_not_override_managed_v2(tmp_path):
    result, recorder = run(tmp_path, pre_version="opencode 1.9.0")
    assert result.returncode == 0, result.stderr
    assert "install" in recorder.read_text()
    assert "v2 verified" in result.stderr


def test_installer_failure_is_failure(tmp_path):
    result, _ = run(tmp_path, install_rc=17)
    assert result.returncode != 0


def test_probe_nonzero_with_broken_channel_fails_closed(tmp_path):
    # A v2-versioned output with a nonzero probe exit is rejected outright;
    # with the install channel also broken there is no self-heal, so the
    # script must fail closed (PROBE_RC drives the managed binary's exit).
    result, _ = run(tmp_path, post_version="opencode v2.4.0")
    assert result.returncode == 0, result.stderr
    (tmp_path / "bin" / "bash").unlink()
    second = subprocess.run(
        ["/bin/bash", str(INSTALLER)],
        env={
            **os.environ,
            "HOME": str(tmp_path / "home"),
            "PATH": str(tmp_path / "bin"),
            "DOTFILES_RUN_OPENCODE_TOOLS_SETUP": "1",
            "PROBE_RC": "4",
        },
        capture_output=True,
        text=True,
    )
    assert second.returncode != 0


def test_major_one_rejected_when_installer_cannot_replace_it(tmp_path):
    result, _ = run(tmp_path, pre_version="opencode 1.9.0", install_rc=1)
    assert result.returncode != 0


def test_second_run_is_verification_only(tmp_path):
    result, recorder = run(tmp_path, post_version="opencode 2.4.0")
    assert result.returncode == 0, result.stderr
    assert recorder.read_text().count("install") == 1
    second = subprocess.run(
        ["/bin/bash", str(INSTALLER)],
        env={
            **os.environ,
            "HOME": str(tmp_path / "home"),
            "PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin",
            "DOTFILES_RUN_OPENCODE_TOOLS_SETUP": "1",
            "POST_VERSION": "opencode 2.4.0",
        },
        capture_output=True,
        text=True,
    )
    assert second.returncode == 0
    assert "verification-only" in second.stderr
    assert recorder.read_text().count("install") == 1


def test_gate_off_has_no_writes(tmp_path):
    result, recorder = run(tmp_path, gate="0", pre_version="opencode 1.9.0")
    assert result.returncode == 0
    assert not recorder.exists()


def test_installer_absent_channel_fails_closed(tmp_path):
    # No bash channel stub on PATH and no curl on a stubs-only PATH: the
    # install channel is unavailable. The script must fail rather than
    # claim success — and must never touch the real network.
    result, _ = run(tmp_path, install_rc="absent", path_mode="isolated")
    assert result.returncode != 0


def test_web_template_uses_stable_absolute_destination():
    template = (
        ROOT / ".chezmoiscripts/run_onchange_26-install-opencode-web.sh.tmpl"
    ).read_text()
    assert 'OPENCODE_BIN="$HOME/.opencode/bin/opencode"' in template
    assert "command -v opencode" not in template
    assert 'exec "${OPENCODE_BIN}" serve' in template


def test_host_template_invokes_the_helper_explicitly():
    template = (
        ROOT / ".chezmoiscripts/run_onchange_06-install-opencode-host.sh.tmpl"
    ).read_text()
    assert "bash " in template and "scripts/install-opencode-v2.sh" in template
    assert template.strip().splitlines()[-1].startswith("bash ")


def test_mode_pinned_already_present_is_noop(tmp_path):
    result, recorder_path = run(
        tmp_path, pre_version="opencode 2.0.31", args=["--version", "2.0.31"]
    )
    assert result.returncode == 0, result.stderr
    # No install recording at all is the strongest no-op evidence.
    assert recorder_path.exists() is False or "install" not in recorder_path.read_text()
    assert "2.0.31 already installed" in result.stderr


def test_mode_pinned_mismatch_installs_exact_version(tmp_path):
    result, recorder = run(
        tmp_path,
        pre_version="opencode 1.19.5",
        post_version="opencode 2.0.31",
        args=["--version", "2.0.31"],
    )
    assert result.returncode == 0, result.stderr
    assert "install" in recorder.read_text()


def test_mode_pinned_post_version_mismatch_fails_closed(tmp_path):
    result, _ = run(
        tmp_path,
        pre_version="opencode 1.19.5",
        post_version="opencode 2.2.5",
        args=["--version", "2.0.31"],
    )
    assert result.returncode != 0
    assert "does not verify as the pinned version" in result.stderr


def test_mode_verify_never_installs(tmp_path):
    result, recorder = run(tmp_path, pre_version="opencode 2.2.5", args=["--verify"])
    assert result.returncode == 0, result.stderr
    assert recorder.exists() is False or "install" not in recorder.read_text()
    (tmp_path / "bin" / "bash").unlink()
    # A v1 host + verify = exit 1 with no install attempt.
    home = tmp_path / "home"
    (tmp_path / "bin" / "opencode").write_text(
        '#!/bin/sh\nprintf "opencode 1.19.5\\n"\n'
    )
    (tmp_path / "bin" / "opencode").chmod(0o755)
    second = subprocess.run(
        ["/bin/bash", str(INSTALLER), "--verify"],
        env={
            **os.environ,
            "HOME": str(home),
            "PATH": str(tmp_path / "bin"),
            "DOTFILES_RUN_OPENCODE_TOOLS_SETUP": "1",
        },
        capture_output=True,
        text=True,
    )
    assert second.returncode == 1
    assert "no stable OpenCode v2 CLI" in second.stderr


def test_mode_install_latest_refreshes_existing_v2(tmp_path):
    result, recorder = run(
        tmp_path, pre_version="opencode 2.0.31", args=["--install-latest"]
    )
    assert result.returncode == 0, result.stderr
    assert "install" in recorder.read_text()


def test_mode_unknown_flag_rejected(tmp_path):
    result, _ = run(tmp_path, args=["--wat"])
    assert result.returncode == 2


def test_mode_pinned_garbage_version_rejected(tmp_path):
    result, _ = run(tmp_path, args=["--version", "9x"])
    assert result.returncode == 2
