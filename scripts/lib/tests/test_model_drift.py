import importlib.util
from pathlib import Path
from unittest.mock import patch

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "check-model-drift.py"
SPEC = importlib.util.spec_from_file_location("check_model_drift", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
DRIFT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIFT)


def test_omlx_drift_is_skipped_when_gate_is_off(monkeypatch):
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "0")
    with patch.object(DRIFT, "check_omlx_daemon") as check_daemon:
        assert DRIFT.check_omlx_models() == []
    check_daemon.assert_not_called()


def test_omlx_drift_skips_when_daemon_is_unreachable(monkeypatch):
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    with (
        patch.object(DRIFT, "deployed_omlx_references", return_value={"missing"}),
        patch.object(DRIFT, "check_omlx_daemon", return_value=(False, "down")),
    ):
        assert DRIFT.check_omlx_models() == []


def test_omlx_drift_skips_catalogue_error_without_false_missing(monkeypatch):
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    with (
        patch.object(DRIFT, "deployed_omlx_references", return_value={"missing"}),
        patch.object(DRIFT, "check_omlx_daemon", return_value=(True, "ok")),
        patch.object(DRIFT, "list_omlx_models", return_value=[]),
        patch.object(DRIFT.omlx, "_get_json", side_effect=RuntimeError("HTTP error")),
    ):
        assert DRIFT.check_omlx_models() == []


def test_omlx_drift_reports_missing_deployed_model(monkeypatch):
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    with (
        patch.object(DRIFT, "deployed_omlx_references", return_value={"missing"}),
        patch.object(DRIFT, "check_omlx_daemon", return_value=(True, "ok")),
        patch.object(DRIFT, "list_omlx_models", return_value=[{"name": "present"}]),
    ):
        assert DRIFT.check_omlx_models() == [
            "oMLX model missing is not present in the live /v1/models catalog"
        ]
