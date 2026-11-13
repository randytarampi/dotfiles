"""Run the actual GitHub review label parser against trusted event fixtures."""

import json
import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/agentic-review.yml"

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="jq required")


def _script():
    text = WORKFLOW.read_text(encoding="utf-8")
    section = text.split("      - name: Parse review request\n", 1)[1]
    return textwrap.dedent(
        section.split("        run: |\n", 1)[1].split(
            "\n  # Native OpenCode GitHub integration:", 1
        )[0]
    )


def _label_event(label="review-junie"):
    return {
        "action": "labeled",
        "repository": {
            "full_name": "randytarampi/dotfiles",
            "owner": {"login": "randytarampi"},
        },
        "sender": {"login": "randytarampi", "type": "User"},
        "pull_request": {
            "number": 295,
            "author_association": "CONTRIBUTOR",
            "user": {"login": "woofbot-randytarampi-ca[bot]"},
        },
        "label": {"name": label},
    }


def _parse(event, tmp_path, **host):
    out = tmp_path / "output"
    out.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "EVENT_JSON": json.dumps(event),
        "INPUT_AGENTS": "",
        "INPUT_PROMPT": "",
        "INPUT_PR_NUMBER": "",
        "INPUT_TOKENS": "",
        "DEFAULT_INFERENCE_PROVIDER": "free",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_ACTOR": "randytarampi",
        "GITHUB_REPOSITORY": "randytarampi/dotfiles",
        "LABEL_EVENT_ACTION": "labeled",
        "LABEL_EVENT_NAME": "review-junie",
        "LABEL_EVENT_SENDER": "randytarampi",
        "LABEL_EVENT_PR": "295",
        "GITHUB_OUTPUT": str(out),
    }
    env.update(host)
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", _script()],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    match = re.search(r"(?m)^agents=(.*)$", out.read_text(encoding="utf-8"))
    assert match
    return match.group(1)


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("review-junie", "junie"),
        ("review-gemini", "gemini"),
        ("review-copilot", "copilot"),
        ("review-all", "junie,copilot"),
        ("review-opencode", ""),  # Deliberately comment-only (issue #289).
        ("unrelated", ""),
    ],
)
def test_human_label_on_bot_authored_pr(tmp_path, label, expected):
    assert (
        _parse(_label_event(label), tmp_path, LABEL_EVENT_NAME=label) == expected
    )


def test_external_pr_author_is_not_the_review_requester(tmp_path):
    event = _label_event()
    event["pull_request"]["author_association"] = "FIRST_TIME_CONTRIBUTOR"
    assert _parse(event, tmp_path) == "junie"


@pytest.mark.parametrize("tamper", ["sender", "bot", "action", "repo", "pr", "label"])
def test_reject_untrusted_label_payload(tmp_path, tamper):
    event = _label_event()
    if tamper == "sender":
        event["sender"]["login"] = "untrusted-user"
    elif tamper == "bot":
        event["sender"]["type"] = "Bot"
    elif tamper == "action":
        event["action"] = "synchronize"
    elif tamper == "repo":
        event["repository"]["full_name"] = "untrusted/fork"
    elif tamper == "pr":
        event["pull_request"]["number"] = 294
    elif tamper == "label":
        event["label"]["name"] = "review-copilot"
    assert _parse(event, tmp_path) == ""


def test_reject_label_when_trusted_host_event_differs(tmp_path):
    event = _label_event()
    assert _parse(event, tmp_path, GITHUB_EVENT_NAME="workflow_dispatch") == ""
    assert _parse(event, tmp_path, LABEL_EVENT_ACTION="opened") == ""
    assert _parse(event, tmp_path, LABEL_EVENT_SENDER="different-user") == ""
    assert _parse(event, tmp_path, GITHUB_ACTOR="different-user") == ""
    assert _parse(event, tmp_path, LABEL_EVENT_PR="294") == ""


def test_comment_trigger_path_remains_intact(tmp_path):
    event = {
        "action": "created",
        "issue": {"number": 295, "pull_request": {"url": "https://example.test"}},
        "comment": {
            "body": "/opencode --pool=opencode Review this PR",
            "author_association": "OWNER",
            "user": {"login": "randytarampi"},
        },
        "repository": {"owner": {"login": "randytarampi"}},
        "sender": {"login": "randytarampi", "type": "User"},
    }
    assert _parse(event, tmp_path, GITHUB_EVENT_NAME="issue_comment") == "opencode"
