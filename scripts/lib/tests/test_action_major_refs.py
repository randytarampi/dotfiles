"""Guard floating-major action refs and the owned dispatcher exception."""

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = ROOT / ".github" / "workflows"
OWNED_REF = "randytarampi/dotfiles/.github/workflows/agentic-review.yml@main"
MAJOR_REF = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[^@\s]+)?@v[0-9]+\Z")


def action_refs():
    for workflow in sorted((*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml"))):
        document = yaml.safe_load(workflow.read_text(encoding="utf-8")) or {}
        jobs = document.get("jobs", {})
        if not isinstance(jobs, dict):
            continue
        path = workflow.relative_to(ROOT).as_posix()
        for job_name, job in jobs.items():
            if not isinstance(job, dict):
                continue
            reusable = job.get("uses")
            if isinstance(reusable, str):
                yield path, f"jobs.{job_name}.uses", reusable
            steps = job.get("steps", [])
            if not isinstance(steps, list):
                continue
            for index, step in enumerate(steps):
                if isinstance(step, dict) and isinstance(step.get("uses"), str):
                    yield path, f"jobs.{job_name}.steps[{index}].uses", step["uses"]


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


def write_fixture_workflow(tmp_path, monkeypatch, name, content):
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    path = workflow_dir / name
    path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    monkeypatch.setattr(sys.modules[__name__], "WORKFLOWS", workflow_dir)
    return path


def test_quoted_inline_refs(tmp_path, monkeypatch):
    write_fixture_workflow(
        tmp_path,
        monkeypatch,
        "inline.yml",
        "jobs:\n"
        "  quoted:\n"
        "    steps:\n"
        '      - {uses: "actions/checkout@v7"}\n'
        "      - {uses: 'google-github-actions/run-gemini-cli@v0'}\n"
        "      - uses: actions/setup-python@v7\n"
        "        with:\n"
        "          uses: actions/checkout@main\n",
    )

    refs = list(action_refs())

    assert [ref for _, _, ref in refs] == [
        "actions/checkout@v7",
        "google-github-actions/run-gemini-cli@v0",
        "actions/setup-python@v7",
    ]
    assert [position for _, position, _ in refs] == [
        "jobs.quoted.steps[0].uses",
        "jobs.quoted.steps[1].uses",
        "jobs.quoted.steps[2].uses",
    ]


def test_inline_branch_rejected(tmp_path, monkeypatch):
    write_fixture_workflow(
        tmp_path,
        monkeypatch,
        "branch.yml",
        "jobs:\n  check:\n    steps:\n      - {uses: actions/checkout@main}\n",
    )

    refs = list(action_refs())

    assert refs == [
        (
            ".github/workflows/branch.yml",
            "jobs.check.steps[0].uses",
            "actions/checkout@main",
        )
    ]
    assert not is_approved_ref(refs[0][0], refs[0][2])


def test_owned_ref_wrong_file(tmp_path, monkeypatch):
    write_fixture_workflow(
        tmp_path,
        monkeypatch,
        "other.yml",
        f"jobs:\n  call:\n    uses: {OWNED_REF}\n",
    )

    ref = next(action_refs())

    assert ref[1] == "jobs.call.uses"
    assert not is_approved_ref(ref[0], ref[2])


def test_comments_and_inputs_ignored(tmp_path, monkeypatch):
    write_fixture_workflow(
        tmp_path,
        monkeypatch,
        "comment.yml",
        "# uses: actions/checkout@main\n"
        "jobs:\n"
        "  run:\n"
        "    steps:\n"
        "      - uses: actions/checkout@v7\n"
        "        with:\n"
        "          uses: actions/checkout@main\n",
    )

    refs = list(action_refs())

    assert [ref for _, _, ref in refs] == ["actions/checkout@v7"]
