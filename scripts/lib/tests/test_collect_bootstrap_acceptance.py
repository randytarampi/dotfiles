import importlib.util
import json
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
        (lambda r: r["host"].update(cpu_model="unknown"), "cpu_model"),
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
