"""Exercise the real notify shell with fake GitHub API responses."""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "agentic-review.yml"

FAKE_GH = """#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
with open(os.environ["MOCK_GH_CALLS"], "a", encoding="utf-8") as output:
    output.write(json.dumps(args) + "\\n")
if "--paginate" in args:
    if os.environ.get("MOCK_GH_FAIL_LIST") == "1":
        print("Simulated comment listing failure", file=sys.stderr)
        sys.exit(42)
    print(os.environ.get("MOCK_GH_COMMENTS", "[]"))
else:
    print("https://github.com/randytarampi/dotfiles/pull/102#issuecomment-42")
"""


def _publish_script():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["notify"]["steps"]
    return next(
        step["run"] for step in steps if step.get("name") == "Publish review bodies"
    )


def _run_notify(
    workdir,
    *,
    review="Substantive clean review",
    result="success",
    token="fake-token",
    comments=None,
    fail_list=False,
):
    workdir.mkdir(parents=True, exist_ok=True)
    review_dir = workdir / "reviews"
    review_dir.mkdir()
    if review is not None:
        (review_dir / "junie-review.md").write_text(review, encoding="utf-8")

    binary_dir = workdir / "bin"
    binary_dir.mkdir()
    gh = binary_dir / "gh"
    gh.write_text(FAKE_GH, encoding="utf-8")
    gh.chmod(0o755)

    log = workdir / "api-calls.jsonl"
    env = dict(os.environ)
    env.update(
        {
            "PATH": str(binary_dir) + os.pathsep + env["PATH"],
            "GH_TOKEN": token,
            "PR_NUMBER": "102",
            "AGENTS": "junie",
            "TOKENS": "@junie-agent",
            "JUNIE_RESULT": result,
            "GEMINI_RESULT": "skipped",
            "OPCODE_RESULT": "skipped",
            "COPILOT_RESULT": "skipped",
            "GITHUB_REPOSITORY": "randytarampi/dotfiles",
            "MOCK_GH_CALLS": str(log),
            "MOCK_GH_COMMENTS": json.dumps(comments or []),
            "MOCK_GH_FAIL_LIST": "1" if fail_list else "0",
        }
    )
    process = subprocess.run(
        ["bash", "-c", _publish_script()],
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    calls = []
    if log.exists():
        calls = [json.loads(line) for line in log.read_text().splitlines()]
    return process, calls


@pytest.mark.parametrize(
    ("review", "result", "token", "expected"),
    [
        (None, "success", "fake-token", "INCOMPLETE"),
        (" \n\t ", "success", "fake-token", "INCOMPLETE"),
        ("Substantive", "failure", "fake-token", "did not succeed"),
        ("Substantive", "success", "", "Missing publication token"),
    ],
)
def test_notify_rejects_incomplete_results_without_publishing(
    tmp_path, review, result, token, expected
):
    process, calls = _run_notify(tmp_path, review=review, result=result, token=token)
    assert process.returncode != 0
    assert expected in process.stderr
    assert calls == []


def test_notify_posts_then_updates_only_its_own_marker(tmp_path):
    created, calls = _run_notify(tmp_path / "created")
    assert created.returncode == 0, created.stderr
    assert any("--method" in call and "POST" in call for call in calls)

    existing = [
        {
            "id": 42,
            "user": {"login": "github-actions[bot]"},
            "body": "<!-- agent-review:junie -->\nOld review",
        }
    ]
    updated, calls = _run_notify(tmp_path / "updated", comments=existing)
    assert updated.returncode == 0, updated.stderr
    assert any("--method" in call and "PATCH" in call for call in calls)
    assert not any("--method" in call and "POST" in call for call in calls)

    existing[0]["user"]["login"] = "a-maintainer"
    other, calls = _run_notify(tmp_path / "different-author", comments=existing)
    assert other.returncode == 0, other.stderr
    assert any("--method" in call and "POST" in call for call in calls)


def test_notify_fails_closed_when_comment_lookup_fails(tmp_path):
    process, calls = _run_notify(tmp_path, fail_list=True)
    assert process.returncode != 0
    assert "Simulated comment listing failure" in process.stderr
    assert any("--paginate" in call for call in calls)
    assert not any("--method" in call for call in calls)
