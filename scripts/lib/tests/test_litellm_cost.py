import datetime as dt
import json

import pytest

import litellm_cost


def test_base_url_port_resolution():
    assert litellm_cost.resolve_base_url({}) == "http://127.0.0.1:4000"
    assert (
        litellm_cost.resolve_base_url({"LITELLM_PORT": "4100"})
        == "http://127.0.0.1:4100"
    )


@pytest.mark.parametrize("url", ["https://127.0.0.1:4000", "http://example.com"])
def test_reject_non_loopback(url):
    with pytest.raises(ValueError):
        litellm_cost._request(url, "/spend/logs/v2", "secret")


def test_parse_window():
    start, end = litellm_cost.parse_window("24h")
    assert (
        dt.datetime.fromisoformat(end.replace("Z", "+00:00"))
        - dt.datetime.fromisoformat(start.replace("Z", "+00:00"))
    ).total_seconds() == 86400
    assert litellm_cost.format_endpoint_date(start) == start[:10] + " " + start[11:19]


def test_paginated_spend(monkeypatch):
    calls = []
    payloads = [
        {
            "data": [{"startTime": "2025-01-01", "spend": 1}],
            "page": 1,
            "total_pages": 2,
            "total": 2,
        },
        {
            "data": [{"startTime": "2025-01-02", "spend": 2}],
            "page": 2,
            "total_pages": 2,
            "total": 2,
        },
    ]
    monkeypatch.setattr(
        litellm_cost,
        "_request",
        lambda *args, **kwargs: (calls.append(args[3]["page"]) or payloads.pop(0)),
    )
    assert litellm_cost.fetch_spend("http://localhost", "key", "a", "b") == {
        "logs": [
            {"startTime": "2025-01-01", "spend": 1},
            {"startTime": "2025-01-02", "spend": 2},
        ],
        "total": 2,
    }
    assert calls == [1, 2]


def test_paginated_spend_rejects_incomplete_count(monkeypatch):
    monkeypatch.setattr(
        litellm_cost,
        "_request",
        lambda *args, **kwargs: {
            "data": [],
            "page": 1,
            "total_pages": 1,
            "total": 5,
        },
    )
    with pytest.raises(RuntimeError, match="received 0 rows, expected 5"):
        litellm_cost.fetch_spend("http://localhost", "key", "a", "b")


def test_safe_error_message_scrubs_key():
    assert "sk-FAKE-marker" not in litellm_cost.safe_error_message(
        RuntimeError("Invalid header value Bearer sk-FAKE-marker"), "sk-FAKE-marker"
    )


def test_provider_grouping_prefix():
    assert litellm_cost.group_rows(
        [{"model": "openrouter/xyz", "spend": 1}, {"model": "bare", "spend": 2}],
        "provider",
    ) == {
        "openrouter": {"spend": 1.0, "tokens": 0},
        "unknown": {"spend": 2.0, "tokens": 0},
    }


def test_client_alias_join():
    assert litellm_cost.group_rows(
        [{"api_key": "hash", "model": "m", "spend": 2, "tokens": 12}],
        "client",
        {"hash": "pi"},
    )["pi"] == {"spend": 2.0, "tokens": 12}


def test_dry_run_has_no_network(monkeypatch):
    import runpy
    import sys

    monkeypatch.setenv("LITELLM_MASTER_KEY", "secret")
    monkeypatch.setattr(
        litellm_cost, "fetch_key_aliases", lambda *args: pytest.fail("network")
    )
    monkeypatch.setattr(
        litellm_cost, "fetch_spend", lambda *args: pytest.fail("network")
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["litellm-costs.py", "--dry-run", "--by", "client", "--window", "7d"],
    )
    with pytest.raises(SystemExit) as error:
        runpy.run_path("scripts/litellm-costs.py", run_name="__main__")
    assert error.value.code == 0


def test_day_grouping_from_start_times(monkeypatch, capsys):
    import sys
    import runpy

    monkeypatch.setenv("LITELLM_MASTER_KEY", "secret")
    monkeypatch.setattr(litellm_cost, "fetch_key_aliases", lambda *args: {})
    monkeypatch.setattr(
        litellm_cost,
        "fetch_spend",
        lambda *args, **kwargs: {
            "logs": [
                {"startTime": "2025-01-01 10:00:00", "model": "m", "spend": 1.5},
                {"startTime": "2025-01-01T11:00:00Z", "model": "m", "spend": 0.5},
                {"startTime": "2025-01-02T09:00:00Z", "model": "m", "spend": 2.0},
            ],
            "total": 3,
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["litellm-costs.py", "--by", "day", "--window", "7d", "--json"],
    )
    with pytest.raises(SystemExit) as error:
        runpy.run_path("scripts/litellm-costs.py", run_name="__main__")
    assert error.value.code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["day"] == {
        "2025-01-01": {"spend": 2.0, "tokens": 0},
        "2025-01-02": {"spend": 2.0, "tokens": 0},
    }
    assert output["total"] == 3


@pytest.mark.parametrize(
    "args",
    [
        ["--window", "bogus"],
        ["--window", "0d"],
        ["--by", "bogus"],
        ["--start", "2026-01-01"],
        ["--start", "01/02/2026", "--end", "2026-01-03"],
        ["--start", "2026-02-30", "--end", "2026-03-01"],
        ["--start", "2026-03-02", "--end", "2026-03-01"],
        ["--endpoint", "http://example.com:4000"],
        ["--by", "bogus", "--window", "7d"],
    ],
)
def test_usage_errors_exit_2(args):
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    script = root / "scripts/litellm-costs.py"
    assert script.is_file()
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            *args,
        ],
        env={
            **os.environ,
            # No LITELLM_MASTER_KEY: usage validation must fire before
            # credential resolution (Gate 1b attempt-3 finding 8).
            "LITELLM_MASTER_KEY": "",
            "HOME": str(root / "scripts/lib/tests/tmp"),
        },
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(root),
    )
    assert result.returncode == 2, result.stderr


@pytest.mark.parametrize(
    "args",
    [
        ["--start", "2026-01-01", "--end", "2026-01-02 12:00:00"],
        ["--start", "2026-01-01 12:00:00", "--end", "2026-01-02"],
    ],
)
def test_mixed_date_formats_no_crash(args):
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    script = root / "scripts/litellm-costs.py"
    assert script.is_file()
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            *args,
        ],
        env={
            **os.environ,
            "LITELLM_MASTER_KEY": "",
            "HOME": str(root / "scripts/lib/tests/tmp"),
        },
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(root),
    )
    # Mixed date-only/datetime formats must parse without TypeError. With
    # credentials absent the run stops at the master-key gate (exit 1),
    # proving the comparison itself did not crash earlier.
    assert result.returncode == 1, result.stderr
    assert "TypeError" not in result.stderr


def test_subprocess_help_runs_real_cli(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    script = root / "scripts/litellm-costs.py"
    assert script.is_file()
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=root,
        env={"PATH": __import__("os").environ["PATH"], "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_text_mode_success_groups_and_total(monkeypatch, caplog):
    import sys
    import runpy

    monkeypatch.setenv("LITELLM_MASTER_KEY", "secret")
    monkeypatch.setattr(litellm_cost, "fetch_key_aliases", lambda *args: {})
    monkeypatch.setattr(
        litellm_cost,
        "fetch_spend",
        lambda *args: {"logs": [{"model": "m", "spend": 1}], "total": 1},
    )
    monkeypatch.setattr(sys, "argv", ["litellm-costs.py", "--window", "7d"])
    with pytest.raises(SystemExit) as error:
        runpy.run_path("scripts/litellm-costs.py", run_name="__main__")
    assert error.value.code == 0
    assert "unknown: spend 1.000000" in caplog.text
    assert "Total rows: 1" in caplog.text


def test_rows_accepts_key_list_envelope():
    # Live /key/list returns {"keys": [...]} (verified against the proxy);
    # the alias fetch must read that envelope, not just data/results.
    assert litellm_cost._rows(
        {"keys": [{"key_alias": "junie", "token": "hash-j"}]}
    ) == [{"key_alias": "junie", "token": "hash-j"}]
    # Spend logs keep their documented envelope.
    assert litellm_cost._rows({"data": [1, 2]}) == [1, 2]
    assert litellm_cost._rows("logs") == []
