"""Hermetic parity tests for update-nvm-globals.py."""

import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
HERMETIC_ENV_VARS = ("HOME", "NVM_DIR", "PATH")


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


def test_installed_versions_matches_shell_filters_and_sorting(updater):
    listing = """
-> v20.11.1
v22.1.0
v18.20.3
default -> v20.11.1
v16.20.2 -> system
N/A
"""
    assert updater._installed_versions("v20.11.1", listing) == [
        "v22.1.0",
        "v18.20.3",
    ]


def test_dry_run_does_not_update_or_change_nvm_state(monkeypatch, tmp_path, updater):
    monkeypatch.setattr(updater.Path, "home", staticmethod(lambda: tmp_path))
    nvm = Mock(
        side_effect=[
            subprocess.CompletedProcess([], 0, "v20.11.1\n", ""),
            subprocess.CompletedProcess([], 0, "-> v20.11.1\nv18.20.3\n", ""),
        ]
    )
    monkeypatch.setattr(updater, "_nvm", nvm)
    monkeypatch.setattr(updater, "_system_npm", lambda: None)

    assert updater.update_globals(dry_run=True) == 0
    assert nvm.call_args_list[0].args[0] == ["version", "default"]
    assert nvm.call_args_list[1].args[0] == ["ls", "--no-colors"]
    assert len(nvm.call_args_list) == 2
    assert not (tmp_path / ".nvm").exists()


def test_updates_default_other_versions_and_system_node(monkeypatch, tmp_path, updater):
    monkeypatch.setattr(updater.Path, "home", staticmethod(lambda: tmp_path))
    system_npm = tmp_path / "brew" / "bin" / "npm"
    system_npm.parent.mkdir(parents=True)
    system_npm.touch()
    system_npm.chmod(0o755)
    (system_npm.parent / "node").touch()
    (system_npm.parent / "node").chmod(0o755)
    monkeypatch.setattr(updater, "_system_npm", lambda: system_npm)
    nvm = Mock(
        side_effect=[
            subprocess.CompletedProcess([], 0, "v20.11.1\n", ""),
            subprocess.CompletedProcess([], 0, "v18.20.3\n", ""),
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 0, "", ""),
        ]
    )
    monkeypatch.setattr(updater, "_nvm", nvm)
    run = Mock(
        side_effect=[
            subprocess.CompletedProcess([], 0, "", ""),  # npm default update
            subprocess.CompletedProcess([], 0, "v1.0.0\n", ""),  # system node
            subprocess.CompletedProcess([], 0, "", ""),  # system npm update
        ]
    )
    monkeypatch.setattr(updater.subprocess, "run", run)

    assert updater.update_globals() == 0
    assert [call.args[0] for call in nvm.call_args_list] == [
        ["version", "default"],
        ["ls", "--no-colors"],
        ["use", "default"],
        ["use", "v18.20.3"],
        ["reinstall-packages", "default"],
        ["use", "default"],
    ]
    assert [call.args[0] for call in run.call_args_list] == [
        ["npm", "update", "-g"],
        [str(system_npm.parent / "node"), "--version"],
        [str(system_npm), "update", "-g"],
    ]
