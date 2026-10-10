import importlib.util
from pathlib import Path
import sys

import pytest

BACKFILL_PATH = Path(__file__).resolve().parents[2] / "litellm-spend-backfill.py"
SPEC = importlib.util.spec_from_file_location("litellm_spend_backfill", BACKFILL_PATH)
assert SPEC is not None
assert SPEC.loader is not None
BACKFILL = importlib.util.module_from_spec(SPEC)
sys.modules["litellm_spend_backfill"] = BACKFILL
SPEC.loader.exec_module(BACKFILL)

RATES = {
    "glm-5.3-flash": {"input_cost_per_token": 1.5e-07, "output_cost_per_token": 5e-07},
    "claude-sonnet-5-5": {
        "input_cost_per_token": 2e-06,
        "output_cost_per_token": 1e-05,
    },
}


def test_wire_to_bare_collapses_every_wire_spelling():
    assert BACKFILL.wire_to_bare("openai/glm-5.3-flash") == "glm-5.3-flash"
    assert BACKFILL.wire_to_bare("ollama-cloud/glm-5.3-flash") == "glm-5.3-flash"
    assert BACKFILL.wire_to_bare("chatgpt/gpt-6-luna") == "gpt-6-luna"
    assert BACKFILL.wire_to_bare("meridian/claude-sonnet-5-5") == "claude-sonnet-5-5"
    assert BACKFILL.wire_to_bare("anthropic/claude-sonnet-5-5") == "claude-sonnet-5-5"
    # Daemon cloud stubs, with and without the ollama/ namespace; dated ids
    # keep their real ':NNNN' suffix (never a stub marker).
    assert BACKFILL.wire_to_bare("glm-5.3-flash:cloud") == "glm-5.3-flash"
    assert BACKFILL.wire_to_bare("ollama/glm-5.3-flash:cloud") == "glm-5.3-flash"
    assert BACKFILL.wire_to_bare("nemotron-3-nano:30b-cloud") == "nemotron-3-nano:30b"
    assert (
        BACKFILL.wire_to_bare("ollama-cloud/deepseek-v4-pro:0813")
        == "deepseek-v4-pro:0813"
    )
    assert BACKFILL.wire_to_bare("") is None
    assert BACKFILL.wire_to_bare(None) is None


def test_expected_spend_recomputes_from_tokens_and_rates():
    priced = {
        "request_id": "r1",
        "model": "ollama/glm-5.3-flash:cloud",
        "spend": 0.0,
        "prompt_tokens": 1000,
        "completion_tokens": 400,
        "cache_hit": "",
    }
    expected = 1000 * 1.5e-07 + 400 * 5e-07
    assert BACKFILL.expected_spend(priced, RATES) == pytest.approx(expected)


def test_expected_spend_rejects_unpriced_cache_and_empty_rows():
    unpriced = {
        "request_id": "r2",
        "model": "omlx/Ornith-1.5-35B-A3B-MLX-4bit",
        "prompt_tokens": 10,
        "completion_tokens": 5,
    }
    assert BACKFILL.expected_spend(unpriced, RATES) is None
    cached = {
        "request_id": "r3",
        "model": "openai/glm-5.3-flash",
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "cache_hit": "True",
    }
    assert BACKFILL.expected_spend(cached, RATES) is None
    empty = {
        "request_id": "r4",
        "model": "openai/glm-5.3-flash",
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }
    assert BACKFILL.expected_spend(empty, RATES) is None


def test_plan_updates_filters_drift_and_keeps_correct_rows():
    drift = [
        {
            "request_id": "drift-1",
            "model": "openai/glm-5.3-flash",
            "spend": 0.0,
            "prompt_tokens": 1000,
            "completion_tokens": 400,
        },
        {
            "request_id": "correct-1",
            "model": "openai/glm-5.3-flash",
            "spend": 1000 * 1.5e-07 + 400 * 5e-07,
            "prompt_tokens": 1000,
            "completion_tokens": 400,
        },
    ]
    plan = BACKFILL.plan_updates(drift, RATES)
    assert [request_id for request_id, _, _ in plan] == ["drift-1"]
    assert plan[0][2] == pytest.approx(1000 * 1.5e-07 + 400 * 5e-07)


def test_help_exits_zero():
    from subprocess import run

    proc = run([sys.executable, str(BACKFILL_PATH), "--help"], capture_output=True)
    assert proc.returncode == 0


def test_dry_run_plans_without_writes(tmp_path, monkeypatch, caplog):
    rows = [
        {
            "request_id": "row-1",
            "model": "ollama/glm-5.3-flash:cloud",
            "spend": 0.0,
            "prompt_tokens": 1000,
            "completion_tokens": 400,
            "cache_hit": "",
        }
    ]
    rates_path = tmp_path / "rates.json"
    rates_path.write_text(
        '{"_provenance": {}, "models": '
        + '{"glm-5.3-flash": {"input_cost_per_token": 1.5e-07, "output_cost_per_token": 5e-07}}'
        + "}"
    )
    applied = []
    monkeypatch.setattr(BACKFILL, "_run_prisma", lambda code, stdin_payload=None: rows)
    monkeypatch.setattr(
        sys,
        "argv",
        ["litellm-spend-backfill.py", "--dry-run", "--rates", str(rates_path)],
    )
    assert BACKFILL.main() == 0
    assert not applied
