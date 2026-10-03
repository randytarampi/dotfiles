"""Guard floating-major action refs and the owned dispatcher exception."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = ROOT / ".github" / "workflows"
OWNED_REF = "randytarampi/dotfiles/.github/workflows/agentic-review.yml@main"
MAJOR_REF = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[^@\s]+)?@v[0-9]+\Z")


def action_refs():
    for workflow in sorted((*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml"))):
        for line_number, line in enumerate(
            workflow.read_text(encoding="utf-8").splitlines(), start=1
        ):
            match = re.match(r"\s*(?:-\s*)?uses:\s*([^\s#]+)", line)
            if match:
                yield workflow.relative_to(ROOT).as_posix(), line_number, match.group(1)


def is_approved_ref(path, value):
    if value == OWNED_REF:
        return path == ".github/workflows/agent-review.yml"
    return bool(MAJOR_REF.fullmatch(value))


def test_refs_major_only():
    refs = list(action_refs())
    workflow_files = {path.name for path in WORKFLOWS.glob("*.yml")} | {
        path.name for path in WORKFLOWS.glob("*.yaml")
    }
    assert len(workflow_files) == 8
    assert refs
    assert all(is_approved_ref(path, ref) for path, _, ref in refs), refs
    assert [ref for _, _, ref in refs].count(OWNED_REF) == 1
    repos = {"/".join(ref.split("@", 1)[0].split("/")[:2]) for _, _, ref in refs}
    assert repos == {
        "actions/checkout",
        "actions/setup-python",
        "actions/cache",
        "actions/upload-artifact",
        "actions/download-artifact",
        "actions/create-github-app-token",
        "github/codeql-action",
        "JetBrains/junie-github-action",
        "google-github-actions/run-gemini-cli",
        "coverallsapp/github-action",
        "qltysh/qlty-action",
        "randytarampi/dotfiles",
    }


def test_refs_reject_nonmajors():
    good = (
        "actions/checkout@v7",
        "google-github-actions/run-gemini-cli@v0",
        "github/codeql-action/init@v4",
        "qltysh/qlty-action/coverage@v2",
    )
    bad = (
        "actions/checkout@main",
        "actions/checkout@v7.0.1",
        "actions/checkout@v7.1",
        "actions/checkout@feature/new-release",
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
    )
    assert all(is_approved_ref(".github/workflows/ci.yml", ref) for ref in good)
    assert not any(is_approved_ref(".github/workflows/ci.yml", ref) for ref in bad)
    assert is_approved_ref(".github/workflows/agent-review.yml", OWNED_REF)
    assert not is_approved_ref(".github/workflows/ci.yml", OWNED_REF)


def test_zizmor_policies_scoped():
    config = yaml.safe_load((ROOT / "zizmor.yml").read_text())
    policies = config["rules"]["unpinned-uses"]["config"]["policies"]
    expected = {
        "actions/checkout",
        "actions/setup-python",
        "actions/cache",
        "actions/upload-artifact",
        "actions/download-artifact",
        "actions/create-github-app-token",
        "github/codeql-action/init",
        "github/codeql-action/analyze",
        "JetBrains/junie-github-action",
        "google-github-actions/run-gemini-cli",
        "coverallsapp/github-action",
        "qltysh/qlty-action/coverage",
    }
    assert {
        repo for repo, policy in policies.items() if policy == "ref-pin"
    } == expected
    owned = "randytarampi/dotfiles/.github/workflows/agentic-review.yml"
    assert policies[owned] == "any"
    assert set(policies) == expected | {owned}
