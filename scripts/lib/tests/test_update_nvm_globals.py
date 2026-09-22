"""Hermetic tests for the single-shell nvm globals updater."""

import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def updater():
    spec = importlib.util.spec_from_file_location(
        "update_nvm_globals", ROOT / "update-nvm-globals.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_one_persistent_shell_preserves_nvm_state(updater, tmp_path, monkeypatch):
    nvm_script = tmp_path / "nvm.sh"
    nvm_script.write_text("# fake nvm\n")
    monkeypatch.setattr(updater, "_nvm_script", lambda: nvm_script)
    run = Mock(
        return_value=subprocess.CompletedProcess(
            [],
            0,
            "NVMUPD default=v20.11.1\n"
            "NVMUPD lane=default status=ok detail=updated\n"
            "NVMUPD lane=propagate:v18.20.3 status=ok detail=reinstalled\n"
            "NVMUPD lane=system status=warn detail=npm-update-failed\n"
            "NVMUPD lane=final status=ok detail=default-restored\n",
            "",
        )
    )
    monkeypatch.setattr(updater.subprocess, "run", run)

    assert updater.update_globals() == 0
    assert run.call_count == 1
    command = run.call_args.args[0]
    assert command[:2] == ["bash", "-c"]
    composed = command[2]
    assert composed.count("source ") == 1
    assert "nvm use default" in composed
    assert "nvm reinstall-packages default" in composed
    assert "npm update -g" in composed


def test_dry_run_composes_no_mutating_commands(updater, tmp_path, monkeypatch):
    nvm_script = tmp_path / "nvm.sh"
    nvm_script.write_text("# fake nvm\n")
    monkeypatch.setattr(updater, "_nvm_script", lambda: nvm_script)
    monkeypatch.setattr(
        updater.subprocess,
        "run",
        Mock(
            return_value=subprocess.CompletedProcess(
                [],
                0,
                "NVMUPD default=v20.11.1\nNVMUPD lane=final status=ok detail=dry-run\n",
                "",
            )
        ),
    )

    assert updater.update_globals(dry_run=True) == 0
    composed = updater.subprocess.run.call_args.args[0][2]
    assert composed.count("source ") == 1
    assert "npm update -g" not in composed
    assert "nvm reinstall-packages" not in composed
    assert "nvm use" not in composed


def test_shell_launch_failure_warns_and_continues(
    updater, tmp_path, monkeypatch, caplog
):
    nvm_script = tmp_path / "nvm.sh"
    nvm_script.write_text("# fake nvm\n")
    monkeypatch.setattr(updater, "_nvm_script", lambda: nvm_script)
    monkeypatch.setattr(
        updater.subprocess, "run", Mock(side_effect=FileNotFoundError("bash"))
    )

    assert updater.update_globals() == 0
    assert "Could not run nvm update shell" in caplog.text


def test_fatal_probe_is_runtime_failure(updater, tmp_path, monkeypatch):
    nvm_script = tmp_path / "nvm.sh"
    nvm_script.write_text("# fake nvm\n")
    monkeypatch.setattr(updater, "_nvm_script", lambda: nvm_script)
    monkeypatch.setattr(
        updater.subprocess,
        "run",
        Mock(
            return_value=subprocess.CompletedProcess(
                [], 1, "NVMUPD fatal=no-default\n", ""
            )
        ),
    )

    assert updater.update_globals() == 1
