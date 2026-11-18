import importlib.util
import struct
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/report-executable-architecture.py"
SPEC = importlib.util.spec_from_file_location("report_executable_architecture", SCRIPT)
REPORTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPORTER)


def pe(machine):
    data = bytearray(70)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 64)
    data[64:68] = b"PE\0\0"
    struct.pack_into("<H", data, 68, machine)
    return bytes(data)


def test_pe_machine_table_and_unknown_code():
    for code, name in REPORTER.MACHINES.items():
        assert REPORTER.pe_architecture_from_bytes(pe(code)) == (name, None)
    assert REPORTER.pe_architecture_from_bytes(pe(0x1234))[0] == "unknown (0x1234)"


def test_invalid_pe_data_is_unknown():
    fixtures = [b"", b"MZ", bytes(64), bytearray(70)]
    data = bytearray(pe(0x8664))
    struct.pack_into("<I", data, 0x3C, 0xFFFFFFFF)
    fixtures.append(data)
    data = bytearray(pe(0x8664))
    data[:2] = b"NO"
    fixtures.append(data)
    data = bytearray(pe(0x8664))
    data[64:68] = b"NOPE"
    fixtures.append(data)
    data = bytearray(pe(0x8664))
    struct.pack_into("<I", data, 0x3C, 1024 * 1024 + 1)
    fixtures.append(data)
    for fixture in fixtures:
        assert REPORTER.pe_architecture_from_bytes(fixture)[0] == "unknown"


def test_make_shim_target_and_launcher_are_separate(tmp_path):
    shim = tmp_path / "make.exe"
    shim.write_bytes(pe(0x014C))
    target = tmp_path / "actual make.exe"
    target.write_bytes(pe(0x8664))
    (tmp_path / "make.exe.shim").write_text(str(target), encoding="utf-8")
    report = REPORTER.build_report({"make": str(shim)})
    assert report["make"]["shim"]["architecture"] == "i386"
    assert report["make"]["target"]["path"] == str(target)
    assert report["make"]["target"]["architecture"] == "AMD64"
    assert (
        report["make"]["target"]["architecture"]
        != report["make"]["shim"]["architecture"]
    )


def test_bash_launcher_not_reported_as_running_target(monkeypatch, tmp_path):
    launcher = tmp_path / "bash.exe"
    implementation = tmp_path / "bash-implementation.exe"
    launcher.write_bytes(pe(0x014C))
    implementation.write_bytes(pe(0xAA64))
    monkeypatch.setattr(REPORTER.shutil, "which", lambda name: str(launcher))
    # A PATH launcher, even after readlink, cannot prove the running shell.
    report = REPORTER.build_report({})
    assert report["bash"]["architecture"] == "unknown"
    assert report["bash"]["path"] is None
    # The actual current shell is supplied by CI using $BASH.
    report = REPORTER.build_report({"bash": str(implementation)})
    assert report["bash"]["path"] == str(implementation)
    assert report["bash"]["architecture"] == "ARM64"


def test_windows_ci_architecture_step_and_required_gate_unchanged():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "Report Windows deployment executable architectures" in workflow
    assert 'python scripts/report-executable-architecture.py --json --path "bash=$BASH"' in workflow
    assert "name: ci/required" in workflow
    assert "needs: [verify, deploy, bootstrap-smoke, bootstrap-networkless]" in workflow
    assert workflow.count("architecture:") >= 6


def test_home_prefix_is_masked_in_report(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    binary = home / "bin" / "chezmoi"
    binary.parent.mkdir()
    binary.write_bytes(pe(0xAA64))
    monkeypatch.setenv("HOME", str(home))
    masked = REPORTER.display_path(binary)
    assert masked.startswith("~/")
    assert str(home) not in masked
    assert REPORTER.display_path("/usr/bin/python") == "/usr/bin/python"


def test_missing_path_reports_unknown_with_reason(tmp_path):
    architecture, reason = REPORTER.pe_architecture(tmp_path / "absent.exe")
    assert architecture == "unknown"
    assert "cannot read executable" in reason


def test_posix_shell_path_resolves_through_windows_form(tmp_path, monkeypatch):
    """A Git Bash `/usr/bin/bash` must not become a permanent `unknown`."""
    windows = tmp_path / "bash.exe"
    windows.write_bytes(pe(0x8664))
    monkeypatch.setattr(
        REPORTER.shutil,
        "which",
        lambda name: "/usr/bin/cygpath" if name == "cygpath" else None,
    )

    def fake_run(command, **kwargs):
        if command[:2] == ["cygpath", "-w"]:
            return subprocess.CompletedProcess(command, 0, f"{windows}\n", "")
        raise AssertionError(f"unexpected subprocess: {command}")

    monkeypatch.setattr(REPORTER.subprocess, "run", fake_run)
    architecture, reason = REPORTER.pe_architecture("/usr/bin/bash-msys-only")
    assert architecture == "AMD64"
    assert reason is None

def test_unresolved_shim_does_not_inherit_wrapper_architecture(tmp_path):
    shim = tmp_path / "make.exe"
    shim.write_bytes(pe(0x014C))
    report = REPORTER.build_report({"make": str(shim)})
    assert report["make"]["shim"]["architecture"] == "i386"
    assert report["make"]["target"]["architecture"] == "unknown"


def test_cygpath_bridge_timeout_is_bounded():
    script = SCRIPT.read_text(encoding="utf-8")
    assert "timeout=5" in script


def test_other_windows_home_path_is_redacted():
    assert "Alice" not in REPORTER.display_path(r"C:\Users\Alice\bin\chezmoi.exe")
