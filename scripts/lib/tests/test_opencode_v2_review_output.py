"""Validate the real v2 JSONL review parser without running an inference model."""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


def _parser_script():
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/agentic-review.yml").read_text(encoding="utf-8")
    )
    run = next(
        step["run"]
        for step in workflow["jobs"]["opencode"]["steps"]
        if step.get("name") == "Execute isolated read-only OpenCode v2"
    )
    return run.split("python3 - <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]


def _execute(tmp_path, events):
    file = tmp_path / "opencode-review.jsonl"
    file.write_text(
        "\n".join(json.dumps(event) for event in events) + "\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["python3", "-c", _parser_script()],
        cwd=ROOT,
        env={
            **os.environ,
            "RUNNER_TEMP": str(tmp_path),
            "REVIEWED_HEAD": "c" * 40,
        },
        text=True,
        capture_output=True,
        check=False,
    )
    return result


def _valid_events():
    return [
        {
            "type": "tool_use",
            "part": {"tool": "read", "state": {"status": "completed"}},
        },
        {"type": "step_start", "part": {"type": "step-start"}},
        {
            "type": "text",
            "part": {
                "type": "text",
                "text": "Blocker: source.py:42 dereferences an unchecked optional value. "
                "Guard the absent case and exercise it in the unit test.",
            },
        },
        {"type": "step_finish", "part": {"type": "step-finish"}},
    ]


def test_valid_review_is_head_bound_and_nonempty(tmp_path):
    result = _execute(tmp_path, _valid_events())
    assert result.returncode == 0, result.stderr
    review = (tmp_path / "opencode-review.md").read_text(encoding="utf-8")
    assert "source.py:42" in review
    assert (tmp_path / "opencode-review-head.txt").read_text().strip() == "c" * 40


@pytest.mark.parametrize(
    "tool", ["shell", "bash", "edit", "write", "execute", "subagent"]
)
def test_forbidden_tool_execution_prevents_publication(tmp_path, tool):
    events = _valid_events()
    events.insert(1, {"type": "tool_use", "part": {"tool": tool}})
    result = _execute(tmp_path, events)
    assert result.returncode != 0
    assert not (tmp_path / "opencode-review.md").exists()


@pytest.mark.parametrize(
    "case", ["error", "unfinished", "empty", "synthetic", "broken_json"]
)
def test_incomplete_or_failed_model_output_is_not_publishable(tmp_path, case):
    events = _valid_events()
    if case == "error":
        events.insert(1, {"type": "error", "error": {"message": "upstream 429"}})
    elif case == "unfinished":
        events.pop()
    elif case == "empty":
        events[2]["part"]["text"] = " "
    elif case == "synthetic":
        events[2]["part"]["synthetic"] = True
    elif case == "broken_json":
        (tmp_path / "opencode-review.jsonl").write_text("{bad json\n")
        result = subprocess.run(
            ["python3", "-c", _parser_script()],
            cwd=ROOT,
            env={**os.environ, "RUNNER_TEMP": str(tmp_path)},
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0
        return
    result = _execute(tmp_path, events)
    assert result.returncode != 0
    assert not (tmp_path / "opencode-review.md").exists()
