import importlib.util
import json
import os
import struct
import subprocess
import sys
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
    monkeypatch.setattr(
        REPORTER.subprocess,
        "run",
        lambda *args, **kwargs: type("Result", (), {"stdout": str(implementation)})(),
    )
    report = REPORTER.build_report({})
    assert report["bash"]["path"] == str(implementation)
    assert report["bash"]["architecture"] == "ARM64"


def test_windows_ci_architecture_step_and_required_gate_unchanged():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "Report Windows deployment executable architectures" in workflow
    assert "python scripts/report-executable-architecture.py --json" in workflow
    assert "name: ci/required" in workflow
    assert "needs: [verify, deploy, bootstrap-smoke, bootstrap-networkless]" in workflow
    assert workflow.count("architecture:") >= 6
