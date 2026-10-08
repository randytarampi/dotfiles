import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import patch

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "litellm-db-prune.py"
SPEC = importlib.util.spec_from_file_location("litellm_db_prune", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
DB_PRUNE = importlib.util.module_from_spec(SPEC)
sys.modules["litellm_db_prune"] = DB_PRUNE
SPEC.loader.exec_module(DB_PRUNE)


def _config(tmp_path, names):
    config = tmp_path / "config.yaml"
    lines = "\n".join(f'  - model_name: "{name}"' for name in names) + "\n"
    config.write_text(lines)
    return config


def test_served_model_names_parses_config(tmp_path):
    config = _config(tmp_path, ["openai/gpt-5.5", "google/models/gemini-3.8-flash"])
    assert DB_PRUNE.served_model_names(config) == {
        "openai/gpt-5.5",
        "google/models/gemini-3.8-flash",
    }


def test_served_model_names_refuses_empty_config(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("# no model entries\n")
    try:
        DB_PRUNE.served_model_names(config)
        raise AssertionError("expected RuntimeError for empty served set")
    except RuntimeError as error:
        assert "refusing to operate against an empty served set" in str(error)


def test_plan_deletions_filters_unserved_rows():
    rows = [
        {"id": "1", "model_name": "openai/inclusionai/llama-3"},
        {"id": "2", "model_name": "openai/gpt-5.5"},
        {"id": "3", "model_name": "meta/llama-3.1-405b-instruct"},
    ]
    stale = DB_PRUNE.plan_deletions(rows, {"openai/gpt-5.5"})
    assert [row["id"] for row in stale] == ["1", "3"]


def test_plan_deletions_keeps_all_when_served():
    rows = [{"id": "1", "model_name": "openai/gpt-5.5"}]
    assert DB_PRUNE.plan_deletions(rows, {"openai/gpt-5.5"}) == []


def test_plan_deletions_skips_rows_without_name():
    rows = [{"id": "1"}, None, {"model_name": "x"}]
    assert DB_PRUNE.plan_deletions(rows, {"x"}) == []


def test_help_exits_zero():
    assert DB_PRUNE.main.__name__ == "main"


def test_main_warns_for_non_db_managed_rows(tmp_path, caplog):
    config = _config(tmp_path, ["openai/gpt-5.5"])
    rows = [{"model_name": "openrouter/inclusionai/ling-3.0-flash-sante:free"}]
    with patch.object(DB_PRUNE, "DEFAULT_CONFIG_PATH", str(config)):
        with patch.object(DB_PRUNE.sys, "argv", ["litellm-db-prune.py"]):
            with patch.object(
                DB_PRUNE.litellm_cost, "resolve_master_key", return_value="sk-test"
            ):
                with patch.object(
                    DB_PRUNE.litellm_cost, "validate_master_key", lambda value: None
                ):
                    with patch.object(DB_PRUNE, "model_info_rows", return_value=rows):
                        result = DB_PRUNE.main()
    assert result == 0
    assert any(
        "not DB-managed (no model-info id)" in record.message
        for record in caplog.records
    )


def test_main_rejects_non_loopback_endpoint(tmp_path, capsys):
    config = _config(tmp_path, ["openai/gpt-5.5"])
    with patch.object(DB_PRUNE, "DEFAULT_CONFIG_PATH", str(config)):
        with patch.object(
            DB_PRUNE.sys,
            "argv",
            ["litellm-db-prune.py", "--endpoint", "http://example.com:4000"],
        ):
            try:
                DB_PRUNE.main()
            except SystemExit as exc:
                assert exc.code == 2
        assert "refusing non-loopback endpoint" in capsys.readouterr().err
