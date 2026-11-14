"""Exercise the actual reusable review event parser against representative events.

Static string assertions cannot prove which reviewers are selected at runtime.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/agentic-review.yml"


def _parse(tmp_path, event):
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    script = workflow["jobs"]["parse"]["steps"][0]["run"]
    output_file = tmp_path / "github-output"
    env = {
        **os.environ,
        "EVENT_JSON": json.dumps(event),
        "INPUT_AGENTS": "",
        "INPUT_PROMPT": "",
        "INPUT_PR_NUMBER": "",
        "INPUT_TOKENS": "",
        "DEFAULT_INFERENCE_PROVIDER": "free",
        "GITHUB_OUTPUT": str(output_file),
        # Host-controlled context, independent of reusable event_json input.
        "GITHUB_EVENT_NAME": (
            "pull_request_target" if "label" in event else "issue_comment"
        ),
        "GITHUB_ACTOR": event["sender"]["login"],
        "GITHUB_REPOSITORY": "randytarampi/dotfiles",
        "LABEL_EVENT_ACTION": event.get("action", ""),
        "LABEL_EVENT_NAME": event.get("label", {}).get("name", ""),
        "LABEL_EVENT_SENDER": event["sender"]["login"],
        "LABEL_EVENT_PR": str(event.get("pull_request", {}).get("number", "")),
    }
    result = subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    outputs = {}
    if output_file.exists():
        for line in output_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("prompt<<"):
                key, value = line.split("=", 1)
                outputs[key] = value
    return result, outputs


@pytest.mark.parametrize(
    ("association", "requested_label", "expected"),
    [
        ("OWNER", "review-all", "junie,copilot"),
        ("MEMBER", "review-all", "junie,copilot"),
        ("COLLABORATOR", "review-all", "junie,copilot"),
        (None, "review-all", "junie,copilot"),
        ("OWNER", "review-gemini", "gemini"),
        ("OWNER", "review-junie", "junie"),
        ("OWNER", "review-copilot", "copilot"),
        ("CONTRIBUTOR", "review-all", "junie,copilot"),
        ("OWNER", "review-opencode", "opencode"),
    ],
)
def test_actual_label_parser_selects_only_eligible_reviewers(
    tmp_path, association, requested_label, expected
):
    event = {
        "action": "labeled",
        "label": {"name": requested_label},
        "pull_request": {"number": 42, "author_association": association},
        "repository": {"full_name": "randytarampi/dotfiles"},
        "sender": {"login": "maintainer", "type": "User"},
    }
    result, outputs = _parse(tmp_path, event)
    assert result.returncode == 0, result.stderr
    assert outputs["agents"] == expected
    assert outputs["pr_number"] == "42"


def test_unauthorised_bot_label_does_not_dispatch_review_all(tmp_path):
    event = {
        "action": "labeled",
        "label": {"name": "review-all"},
        "pull_request": {"number": 42, "author_association": None},
        "repository": {"full_name": "randytarampi/dotfiles"},
        "sender": {"login": "test-bot[bot]", "type": "Bot"},
    }
    result, outputs = _parse(tmp_path, event)
    assert result.returncode == 0, result.stderr
    assert outputs["agents"] == ""


def test_authorised_opencode_comment_does_not_trigger_gemini(tmp_path):
    event = {
        "issue": {"number": 99, "pull_request": {"url": "https://example.invalid"}},
        "comment": {
            "body": "/opencode --pool=free",
            "author_association": "OWNER",
            "user": {"login": "maintainer"},
        },
        "repository": {"owner": {"login": "maintainer"}},
        "sender": {"login": "maintainer"},
    }
    result, outputs = _parse(tmp_path, event)
    assert result.returncode == 0, result.stderr
    assert outputs["agents"] == "opencode"
    assert outputs["inference_provider"] == "free"
