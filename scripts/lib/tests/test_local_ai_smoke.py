import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "verify-local-ai-smoke.py"
SPEC = importlib.util.spec_from_file_location("local_ai_smoke", SCRIPT)
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


def _snapshot(tmp_path, config_models, ledger_models):
    config = tmp_path / "config.yaml"
    config.write_text(
        "model_list:\n" + "".join(f'- model_name: "{name}"\n' for name in config_models)
    )
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps([{"model_name": name} for name in ledger_models]))
    notes = tmp_path / "notes.json"
    notes.write_text(json.dumps(["UNKNOWN: catalogue absent/expired"]))
    return smoke.validate_snapshot(config, ledger, notes)


def test_retained_ledger_and_target_pass(tmp_path):
    models = [smoke.TARGET_ALIAS, "existing/model"]
    report = _snapshot(tmp_path, models, models)
    assert report["ledger_retained_exactly"]
    assert report["target_present"]
    assert report["no_invented_models"]


def test_ledger_missing_target_fails(tmp_path):
    report = _snapshot(tmp_path, ["existing/model"], ["existing/model"])
    assert not report["target_present"]


def test_invented_model_fails(tmp_path):
    report = _snapshot(
        tmp_path, [smoke.TARGET_ALIAS, "invented/model"], [smoke.TARGET_ALIAS]
    )
    assert not report["ledger_retained_exactly"]
    assert not report["no_invented_models"]


def test_help_exits_zero(monkeypatch):
    import pytest

    with pytest.raises(SystemExit) as result:
        smoke.main(["--help"])
    assert result.value.code == 0
