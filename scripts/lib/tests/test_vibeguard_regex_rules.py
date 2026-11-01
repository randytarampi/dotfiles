import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_opencode_vibeguard_regex_corpus():
    config = json.loads(
        (ROOT.parent / "configs/opencode/vibeguard.config.json").read_text()
    )
    assert config["patterns"]["keywords"] == []
    patterns = [re.compile(rule["pattern"]) for rule in config["patterns"]["regex"]]
    positives = [
        "sk-proj-" + "A1_b-" * 14,
        "sk-svcacct-" + "Z9_-x" * 12,
        "sk-" + "a" * 48,
    ]
    negatives = [
        "The sk- prefix is mentioned in ordinary prose.",
        "__SECRET__",
        "sk-proj-placeholder",
    ]
    for sample in positives:
        assert any(pattern.search(sample) for pattern in patterns), sample
    for sample in negatives:
        assert not any(pattern.search(sample) for pattern in patterns), sample


def test_plugin_git_refs_accept_main_and_validate_characters():
    from opencode_plugins import is_pinned_spec

    assert is_pinned_spec("github:owner/repo#" + "a" * 40)
    assert is_pinned_spec("github:owner/repo#main")
    assert is_pinned_spec("github:owner/repo#v020")
    assert not is_pinned_spec("github:owner/repo#!!!")
    assert not is_pinned_spec("github:owner/repo#")
