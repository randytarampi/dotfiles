import os
import shlex
import stat
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


SERVICE_HELPERS = (
    ("litellm_service.sh", "litellm_service_stop", "com.litellm.proxy"),
    ("openwebui_service.sh", "openwebui_service_stop", "com.openwebui.web"),
    (
        "openwebui_service.sh",
        "openwebui_terminal_service_stop",
        "com.openwebui.terminal",
    ),
    (
        "openwebui_service.sh",
        "openwebui_computer_service_stop",
        "com.openwebui.computer",
    ),
)


def run_stop(tmp_path, script_name, helper_name):
    fake_home = tmp_path / "home"
    launchctl_dir = tmp_path / "bin"
    launchctl_dir.mkdir()
    log = tmp_path / "launchctl.log"
    launchctl = launchctl_dir / "launchctl"
    launchctl.write_text(
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {shlex.quote(str(log))}\n"
    )
    launchctl.chmod(launchctl.stat().st_mode | stat.S_IXUSR)

    script = os.path.join(ROOT, "scripts", "lib", script_name)
    result = subprocess.run(
        ["bash", "-c", f". {shlex.quote(script)}; {helper_name}"],
        env={
            "HOME": str(fake_home),
            "PATH": os.pathsep.join([str(launchctl_dir), os.environ["PATH"]]),
        },
        capture_output=True,
        text=True,
    )
    return result, fake_home, log


@pytest.mark.parametrize("script_name,helper_name,_label", SERVICE_HELPERS)
def test_stop_is_hermetic_when_fake_home_has_no_plist(
    tmp_path, script_name, helper_name, _label
):
    result, _fake_home, log = run_stop(tmp_path, script_name, helper_name)

    assert result.returncode == 0, result.stderr
    assert not log.exists() or log.read_text() == ""


@pytest.mark.parametrize("script_name,helper_name,label", SERVICE_HELPERS)
def test_stop_boots_out_when_fake_home_plist_exists(
    tmp_path, script_name, helper_name, label
):
    result, fake_home, log = run_stop(tmp_path, script_name, helper_name)
    plist = fake_home / "Library" / "LaunchAgents" / f"{label}.plist"
    plist.parent.mkdir(parents=True)
    plist.touch()

    # Re-run with the deployed plist present; the helper must still attempt
    # teardown, but only in the current user's launchd domain.
    result = subprocess.run(
        [
            "bash",
            "-c",
            f". {shlex.quote(os.path.join(ROOT, 'scripts', 'lib', script_name))}; "
            f"{helper_name}",
        ],
        env={
            "HOME": str(fake_home),
            "PATH": os.pathsep.join([str(log.parent / "bin"), os.environ["PATH"]]),
        },
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines() == [f"bootout gui/{os.getuid()}/{label}"]
