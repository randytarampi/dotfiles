import importlib.util

import pytest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / "check-vibeguard-audit.py"
SPEC = importlib.util.spec_from_file_location("check_vibeguard_audit", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as helper:
        MODULE.main(["--help"])
    assert helper.value.code == 0
    assert "read-only" in capsys.readouterr().out


def test_fix_is_rejected(capsys):
    with pytest.raises(SystemExit) as rejected:
        MODULE.main(["--fix"])
    assert rejected.value.code == 2
    assert "read-only" in capsys.readouterr().err


def test_setup_is_rejected(capsys):
    with pytest.raises(SystemExit) as rejected:
        MODULE.main(["setup"])
    assert rejected.value.code == 2


def test_read_only_arguments_build_pinned_command():
    calls = []

    def record(command):
        calls.append(command)
        return type("Result", (), {"returncode": 0})()

    with patch.object(MODULE.subprocess, "run", side_effect=record):
        assert MODULE.main(["--full", "--changed-only", "repo-path", "--strict"]) == 0
    assert calls == [
        [
            "npx",
            "-y",
            "--package",
            "@taehwandev/vibeguard@26.40.1",
            "vibeguard",
            "audit",
            "repo-path",
            "--full",
            "--changed-only",
            "--strict",
        ]
    ]


def test_unknown_flag_is_usage_error(capsys):
    with pytest.raises(SystemExit) as unknown:
        MODULE.main(["--unknown"])
    assert unknown.value.code == 2
