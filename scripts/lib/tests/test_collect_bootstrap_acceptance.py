import importlib.util
import json
import os
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "collect-bootstrap-acceptance.py"
spec = importlib.util.spec_from_file_location("collector", SCRIPT)
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


def receipt():
    value = collector.inspect("unknown", ".", "a" * 40, [])
    value["host"]["cpu_model"] = "test cpu"
    return value


def test_inspect_shape_and_validation():
    result = receipt()
    assert collector.validate(result)
    assert result["privacy"] == {"secrets_included": False, "raw_paths_included": False}


@pytest.mark.parametrize(
    "change,message",
    [
        (lambda r: r.update(schema_version=2), "schema_version"),
        (lambda r: r.update(source_sha="bad"), "source_sha"),
        (
            lambda r: r["stages"]["deploy"].update(source="/Users/private/.ssh/id_rsa"),
            "stage source",
        ),
        (lambda r: r["stages"]["verify"].update(outcome="maybe"), "outcome"),
        (
            lambda r: r["stages"]["verify"].update(outcome="pass", exit_code=1),
            "exit_code",
        ),
    ],
)
def test_invalid_receipts(change, message):
    value = receipt()
    change(value)
    with pytest.raises(collector.ReceiptError, match=message):
        collector.validate(value)


def test_record_failure_then_success_does_not_pass(tmp_path):
    record = tmp_path / "outcome.json"
    record.write_text(
        json.dumps(
            {
                "stages": {
                    "first_install": [
                        {"outcome": "fail", "exit_code": 1},
                        {"outcome": "pass", "exit_code": 0},
                    ]
                }
            }
        )
    )
    merged = collector.merge_record(receipt(), record)
    assert merged["stages"]["first_install"]["outcome"] == "fail"


def test_stale_source_sha_rejected():
    with pytest.raises(collector.ReceiptError, match="mismatch"):
        collector.validate(receipt(), "b" * 40)


def test_secret_canary_rejected(tmp_path):
    record = tmp_path / "secret.json"
    record.write_text('{"api_key":"canary"}')
    with pytest.raises(collector.ReceiptError, match="secret-like"):
        collector.merge_record(receipt(), record)


def test_atomic_write_refuses_existing_and_symlink(tmp_path):
    target = tmp_path / "receipt.json"
    target.write_text("old")
    with pytest.raises(collector.ReceiptError, match="exists"):
        collector.atomic_write(target, receipt())
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(collector.ReceiptError, match="symlink"):
        collector.atomic_write(link, receipt(), True)


def test_platform_path_classification():
    assert collector.path_class("/opt/homebrew/bin/brew") == "homebrew"
    assert collector.path_class("C:\\Windows\\System32\\cmd.exe") == "system"
    assert collector.path_class("/unknown/path/tool") == "other"


def test_missing_tool_unknown(monkeypatch):
    monkeypatch.setattr(collector.shutil, "which", lambda _: None)
    result = collector.inspect("unknown", ".", "a" * 40, [])
    assert all(tool["version"] == "unknown" for tool in result["tools"])


def test_tool_versions_are_probed_with_version_only(tmp_path, monkeypatch):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    tool = fake_bin / "chezmoi"
    tool.write_text("#!/usr/bin/env bash\necho 'chezmoi version v9.9.9-test'\n")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ.get('PATH', '')}")
    assert collector.tool_version("chezmoi") == "chezmoi version v9.9.9-test"
    assert collector.tool_version("definitely-not-a-real-tool") == "unknown"


def test_tool_version_truncates_and_never_raises(tmp_path, monkeypatch):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    tool = fake_bin / "verbose"
    tool.write_text(
        "#!/usr/bin/env bash\nprintf 'v%s\\n' \"$(head -c 400 /dev/zero | tr '\\0' 'x')\"\n"
    )
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ.get('PATH', '')}")
    version = collector.tool_version("verbose")
    assert len(version) == collector.VERSION_MAX_CHARS


def test_executable_type_sniffs_magic_bytes(tmp_path):
    cases = {
        "script": b"#!/bin/sh\nexit 0\n",
        "elf": b"\x7fELF\x02\x01\x01\x00",
        "macho": b"\xcf\xfa\xed\xfe\x00\x00\x00\x00",
        "pe": b"MZ\x90\x00\x03\x00\x00\x00",
        "unknown": b"\x00\x01\x02\x03",
    }
    for expected, payload in cases.items():
        target = tmp_path / f"sample-{expected}"
        target.write_bytes(payload)
        assert collector.executable_type(target) == expected
    assert collector.executable_type(tmp_path / "absent") == "unknown"
    assert collector.executable_type("") == "unknown"

def test_unknown_cpu_model_is_valid_on_windows_and_arm_macs():
    result = receipt()
    result["host"]["cpu_model"] = "unknown"
    assert collector.validate(result)


def test_record_rejects_private_values_even_without_secret_field_names(tmp_path):
    record = tmp_path / "record.json"
    record.write_text(
        json.dumps({
            "stages": {
                "deploy": {
                    "outcome": "pass",
                    "exit_code": 0,
                    "source": "manual",
                    "notes": "/Users/tester/.ssh/id_rsa",
                }
            }
        })
    )
    with pytest.raises(collector.ReceiptError, match="personal path"):
        collector.merge_record(receipt(), record)


def test_stage_record_rejects_paths_and_invalid_attempt_shapes(tmp_path):
    record = tmp_path / "record.json"
    record.write_text(
        json.dumps({"stages": {"deploy": {"source": "/home/operator/log.txt"}}})
    )
    with pytest.raises(collector.ReceiptError, match="personal path"):
        collector.merge_record(receipt(), record)
    record.write_text(json.dumps({"stages": {"deploy": ["bad attempt"]}}))
    with pytest.raises(collector.ReceiptError, match="attempt"):
        collector.merge_record(receipt(), record)


def test_inspect_with_unknown_cpu_model_still_validates(monkeypatch):
    monkeypatch.setattr(collector, "_cpu_model", lambda _: "unknown")
    result = collector.inspect("unknown", ".", "a" * 40, [])
    assert collector.validate(result)


def test_version_output_cannot_contain_a_home_path(tmp_path, monkeypatch):
    fake = tmp_path / "private-version-tool"
    fake.write_text("#!/bin/sh\nprintf '/Users/private/.ssh/id_rsa\\n'\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    assert collector.tool_version("private-version-tool") == "redacted"


def test_imported_stage_must_have_only_declared_fields():
    result = receipt()
    result["stages"]["deploy"]["notes"] = "unreviewed"
    with pytest.raises(collector.ReceiptError, match="stage fields"):
        collector.validate(result)
