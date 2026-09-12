import importlib.util
from pathlib import Path
from unittest.mock import patch

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "check-model-drift.py"
SPEC = importlib.util.spec_from_file_location("check_model_drift", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
DRIFT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIFT)


def test_local_engine_drift_is_skipped_when_gate_is_off(monkeypatch):
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "0")
    assert DRIFT.check_local_engine_models() == []


def test_local_engine_drift_skips_when_engine_is_unreachable(monkeypatch):
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    with (
        patch.object(DRIFT, "deployed_engine_references", return_value={"missing"}),
        patch.object(DRIFT, "active_engines", return_value=["omlx"]),
        patch.object(DRIFT, "iter_engine_models_strict", return_value=None),
    ):
        assert DRIFT.check_local_engine_models() == []


def test_local_engine_drift_skips_catalogue_error_without_false_missing(monkeypatch):
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    with (
        patch.object(DRIFT, "deployed_engine_references", return_value={"missing"}),
        patch.object(DRIFT, "active_engines", return_value=["omlx"]),
        patch.object(DRIFT, "iter_engine_models_strict", return_value=None),
    ):
        assert DRIFT.check_local_engine_models() == []


def test_local_engine_drift_reports_missing_deployed_model(monkeypatch):
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    with (
        patch.object(DRIFT, "deployed_engine_references", return_value={"missing"}),
        patch.object(DRIFT, "active_engines", return_value=["omlx"]),
        patch.object(
            DRIFT, "iter_engine_models_strict", return_value=[{"name": "present"}]
        ),
    ):
        assert DRIFT.check_local_engine_models() == [
            "omlx model missing is not present in the live catalogue"
        ]
