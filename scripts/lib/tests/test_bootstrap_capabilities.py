"""Read-only host capability facts must never become operational claims."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/report-bootstrap-capabilities.py"


def _module():
    spec = importlib.util.spec_from_file_location("bootstrap_capabilities", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("x86_64", "amd64"),
        ("AMD64", "amd64"),
        ("aarch64", "arm64"),
        ("arm64", "arm64"),
        ("armv7l", "arm32"),
        ("armv8l", "arm32"),
        ("ppc64le", "power"),
        ("mystery-cpu", "unknown"),
    ],
)
def test_architecture_classifier_is_explicit(raw, normalized):
    assert _module().normalize_arch(raw) == normalized


def test_linux_arm64_is_only_a_candidate_and_has_no_runtime_claim():
    tools = {"bash", "make", "chezmoi", "codex", "systemctl"}
    report = _module().assess("Linux", "aarch64", tools, 8 * 1024**3, "not_advertised")
    assert report["core_l0"] == "candidate_unverified"
    assert report["operational_verified"] is False
    for level in ("packages_l1", "agents_l2", "services_l3"):
        assert report[level] == "not_qualified"
    assert any("not proof of headlessness" in note for note in report["notes"])


@pytest.mark.parametrize("arch", ["armv7l", "ppc64le"])
def test_experimental_hardware_is_not_marked_supported(arch):
    tools = {"bash", "make", "chezmoi"}
    report = _module().assess("Linux", arch, tools, None, "not_advertised")
    assert report["core_l0"] == "experimental_unverified"
    assert report["operational_verified"] is False


@pytest.mark.parametrize("os_name,arch", [("Linux", "mips"), ("Plan9", "amd64")])
def test_unknown_platform_fails_closed(os_name, arch):
    report = _module().assess(
        os_name, arch, {"bash", "make", "chezmoi"}, None, "unknown"
    )
    assert report["core_l0"] == "not_qualified"
    assert report["operational_verified"] is False


def test_missing_core_tool_and_low_ram_do_not_claim_l0_or_local_inference():
    report = _module().assess("Linux", "aarch64", {"bash"}, 512 * 1024**2, "unknown")
    assert report["missing_core_tools"] == ["make", "chezmoi"]
    assert report["core_l0"] == "not_qualified"
    assert any("Memory below 4 GiB" in note for note in report["notes"])


def test_malformed_or_missing_meminfo_is_unknown():
    parse = _module().parse_memtotal_bytes
    assert parse("MemTotal: 131072 kB\n") == 131072 * 1024
    assert parse("MemTotal: unknown kB\n") is None
    assert parse("MemFree: 10 kB\n") is None


def test_os_release_parser_does_not_eval_shell_assignments():
    source = 'ID=ubuntu\nVERSION_ID="24.04"\nUNKNOWN=$(touch /tmp/nope)\n'
    assert _module().parse_os_release(source) == {
        "ID": "ubuntu",
        "VERSION_ID": "24.04",
    }


def test_inventory_never_prints_credentials_or_identifies_arm_board(monkeypatch):
    m = _module()
    monkeypatch.setenv("LITELLM_MASTER_KEY", "synthetic-sensitive-canary")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-sensitive-canary")
    monkeypatch.setattr(m.platform, "system", lambda: "Linux")
    monkeypatch.setattr(m.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(m.platform, "release", lambda: "synthetic")
    monkeypatch.setattr(
        m,
        "read_optional",
        lambda path: (
            "ID=ubuntu\nVERSION_ID=24.04\n"
            if "os-release" in path
            else "MemTotal: 8192000 kB\n"
        ),
    )
    monkeypatch.setattr(m.shutil, "which", lambda name: f"/bin/{name}")
    report = m.inventory()
    encoded = json.dumps(report)
    assert "synthetic-sensitive-canary" not in encoded
    assert "LITELLM_MASTER_KEY" not in encoded
    assert report["host"]["hardware_identity"] == "not_assessed"
    assert report["host"]["architecture"] == "aarch64"
    assert report["assessment"]["operational_verified"] is False
    assert report["host"]["agent_clis_present"]


def test_non_linux_display_variable_is_not_linux_display_evidence(monkeypatch):
    m = _module()
    monkeypatch.setenv("DISPLAY", ":1")
    monkeypatch.setattr(m.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(m.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(m.shutil, "which", lambda name: f"/bin/{name}")
    report = m.inventory()
    assert report["host"]["display_environment"] == "unknown"
    assert report["assessment"]["operational_verified"] is False
    assert report["host"]["service_manager_binary_present"] is True


def test_missing_linux_service_manager_is_presence_only(monkeypatch):
    m = _module()
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(m.platform, "system", lambda: "Linux")
    monkeypatch.setattr(m.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(
        m.shutil,
        "which",
        lambda name: f"/bin/{name}" if name in ("bash", "make", "chezmoi") else None,
    )
    report = m.inventory()
    assert report["host"]["service_manager_binary_present"] is False
    assert report["host"]["display_environment"] == "not_advertised"
    assert report["assessment"]["core_l0"] == "candidate_unverified"
    assert report["assessment"]["services_l3"] == "not_qualified"


def test_json_cli_is_read_only_and_keeps_evidence_tier():
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["schema_version"] == 1
    assert report["assessment"]["operational_verified"] is False
    assert report["assessment"]["evidence"] == "read_only_inventory"
