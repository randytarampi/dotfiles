"""Entrypoint regression tests for manual Paseo metadata writes."""

import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "configure-paseo-metadata.py"
SPEC = importlib.util.spec_from_file_location("configure_paseo_metadata_test", SOURCE)
metadata = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(metadata)


@pytest.fixture
def installed(monkeypatch, tmp_path):
    home = tmp_path / ".paseo"
    home.mkdir(mode=0o700)
    path = home / "config.json"
    original = {"version": 1, "unmanaged": {"keep": True}}
    path.write_text(json.dumps(original), encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.setenv("PASEO_HOME", str(home))
    monkeypatch.delenv(metadata.GATE, raising=False)
    monkeypatch.setattr(metadata.shutil, "which", lambda name: "/bin/" + name)
    monkeypatch.setattr(metadata, "private_json", lambda _path: {})
    monkeypatch.setattr(metadata.tier_registry, "load_registry", lambda: {})
    monkeypatch.setattr(metadata, "list_local_ollama_models", lambda: [])
    monkeypatch.setattr(
        metadata, "resolve_orchestrator", lambda *_args, **_kwargs: "omlx/model"
    )
    return home, path, original


def run_apply():
    return metadata.main(["--preset", "local", "--apply"])


def write_native(home, providers):
    path = home / "config.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("agents", {}).setdefault("metadataGeneration", {})[
        "providers"
    ] = providers
    path.write_text(json.dumps(data), encoding="utf-8")


def test_gate_off_refuses_apply_before_native_calls(installed, monkeypatch):
    _home, path, original = installed

    def forbidden(*_args, **_kwargs):
        pytest.fail("native setter must not run without explicit owner gate")

    monkeypatch.setattr(metadata, "native_set", forbidden)
    assert run_apply() == 1
    assert json.loads(path.read_text()) == original


def test_apply_dry_run_has_no_native_writes(installed, monkeypatch):
    _home, path, original = installed
    monkeypatch.setenv(metadata.GATE, "1")

    def forbidden(*_args, **_kwargs):
        pytest.fail("native setter must not run for dry-run or preview")

    monkeypatch.setattr(metadata, "native_set", forbidden)
    assert metadata.main(["--preset", "local", "--apply", "--dry-run"]) == 0
    assert metadata.main(["--preset", "local"]) == 0
    assert json.loads(path.read_text()) == original


def test_staged_lossy_write_refuses_live_mutation(installed, monkeypatch):
    home, path, original = installed
    monkeypatch.setenv(metadata.GATE, "1")
    calls = []

    def lossy(_binary, target_home, providers):
        calls.append(target_home)
        (target_home / "config.json").write_text(
            json.dumps({"agents": {"metadataGeneration": {"providers": providers}}})
        )

    monkeypatch.setattr(metadata, "native_set", lossy)
    assert run_apply() == 1
    assert calls and home not in calls
    assert json.loads(path.read_text()) == original


def test_successful_staged_and_live_writes_preserve_unrelated(installed, monkeypatch):
    home, path, original = installed
    monkeypatch.setenv(metadata.GATE, "1")
    calls = []

    def native(_binary, target_home, providers):
        calls.append(target_home)
        write_native(target_home, providers)

    monkeypatch.setattr(metadata, "native_set", native)
    assert run_apply() == 0
    assert len(calls) == 2
    assert calls[0] != home and calls[1] == home
    current = json.loads(path.read_text())
    assert current["unmanaged"] == original["unmanaged"]
    assert current["agents"]["metadataGeneration"]["providers"] == [
        {"provider": "pi", "model": "omlx/model"}
    ]
    assert run_apply() == 0
    assert len(calls) == 2  # already qualified; do not repeat writes


@pytest.mark.parametrize("fail_stage", [True, False])
def test_nonzero_native_exit_refuses_even_if_readback_matches(
    installed, monkeypatch, capsys, fail_stage
):
    home, path, original = installed
    monkeypatch.setenv(metadata.GATE, "1")
    calls = []

    def fake_run(argv, **_kwargs):
        target_home = Path(argv[-1])
        calls.append(target_home)
        write_native(target_home, json.loads(argv[5]))
        code = 1 if fail_stage or target_home == home else 0
        return SimpleNamespace(returncode=code)

    monkeypatch.setattr(metadata.subprocess, "run", fake_run)
    assert run_apply() == 1
    assert "native setter failed (exit 1)" in capsys.readouterr().err
    assert len(calls) == (1 if fail_stage else 2)
    if fail_stage:
        assert json.loads(path.read_text()) == original
    else:
        # Partial live writes are never rolled back over a concurrent owner.
        assert json.loads(path.read_text())["unmanaged"] == original["unmanaged"]


def test_native_timeout_is_reported_without_traceback(installed, monkeypatch, capsys):
    _home, path, original = installed
    monkeypatch.setenv(metadata.GATE, "1")

    def timeout(argv, **_kwargs):
        raise subprocess.TimeoutExpired(argv, 60)

    monkeypatch.setattr(metadata.subprocess, "run", timeout)
    assert run_apply() == 1
    output = capsys.readouterr().err
    assert "native setter timed out" in output
    assert "Traceback" not in output
    assert json.loads(path.read_text()) == original
