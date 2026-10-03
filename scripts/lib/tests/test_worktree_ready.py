import os
import shutil
import subprocess
import sys
from pathlib import Path

import worktree_readiness

SCRIPT = Path(__file__).resolve().parents[2] / "verify-worktree-ready.py"
REPO_ROOT = SCRIPT.parents[1]
BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40


def fixture(
    tmp_path,
    *,
    imports=False,
    install_ok=True,
    dirty="",
    branch="feature/test",
    base_ok=True,
):
    project = tmp_path / "dotfiles"
    project.mkdir(parents=True)
    (project / "Makefile").write_text(".DEFAULT_GOAL := help\n", encoding="utf-8")
    contract = project / "scripts/lib/cli-contract.json"
    contract.parent.mkdir(parents=True)
    contract.write_text("{}\n", encoding="utf-8")
    (project / ".python-version").write_text("3.13.13\n", encoding="utf-8")
    (project / "pyproject.toml").write_text(
        '[project]\nname = "dotfiles"\nversion = "0.1.0"\n'
        'requires-python = ">=3.10"\n',
        encoding="utf-8",
    )
    (project / "poetry.lock").write_text(
        '[[package]]\nname = "pytest"\nversion = "9.0.0"\n\n'
        '[metadata]\nlock-version = "2.1"\n',
        encoding="utf-8",
    )
    (project / "dirty.txt").write_text("preserve me\n", encoding="utf-8")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    git = bindir / "git"
    git.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        '  *"rev-parse --show-toplevel"*) printf "%s\\n" "$FAKE_PROJECT" ;;\n'
        '  *"rev-parse HEAD"*) printf "%s\\n" "$FAKE_HEAD" ;;\n'
        '  *"rev-parse --verify"*) [ "$FAKE_BASE_OK" = "1" ] && printf "%s\\n" "$FAKE_BASE" || exit 1 ;;\n'
        '  *"branch --show-current"*) printf "%s\\n" "$FAKE_BRANCH" ;;\n'
        '  *"status --porcelain"*) printf "%s\\n" "$FAKE_DIRTY" ;;\n'
        '  *"merge-base --is-ancestor"*) [ "$FAKE_BASE_OK" = "1" ] ;;\n'
        '  *"ls-files --error-unmatch"*) for arg do last=$arg; done; printf "%s\\n" "$last" ;;\n'
        "  *) exit 1 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    poetry = bindir / "poetry"
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    python = venv / "bin/python"
    python.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n%s\\n" "$FAKE_PY_VERSION" "$FAKE_PYTHON"\n'
        '[ "$FAKE_IMPORTS" = "1" ] || [ -f "$FAKE_READY" ] || exit 1\n',
        encoding="utf-8",
    )
    python.chmod(0o755)
    poetry.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "check" ]; then exit "$FAKE_CHECK_STATUS"; fi\n'
        'if [ "$1" = "env" ]; then printf "%s\\n" "$FAKE_VENV"; exit 0; fi\n'
        'if [ "$1" = "install" ]; then printf "install\\n" >> "$FAKE_CALLS"; if [ "$FAKE_INSTALL_STATUS" = "0" ]; then : > "$FAKE_READY"; fi; exit "$FAKE_INSTALL_STATUS"; fi\n'
        "exit 2\n",
        encoding="utf-8",
    )
    git.chmod(0o755)
    poetry.chmod(0o755)
    (bindir / "python3").write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8"
    )
    (bindir / "python3").chmod(0o755)
    calls = tmp_path / "calls"
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "PATH": f"{bindir}:/usr/bin:/bin",
        "FAKE_PROJECT": str(project),
        "FAKE_HEAD": HEAD_SHA,
        "FAKE_BASE": BASE_SHA,
        "FAKE_BASE_OK": "1" if base_ok else "0",
        "FAKE_BRANCH": branch,
        "FAKE_DIRTY": dirty,
        "FAKE_IMPORTS": "1" if imports else "0",
        "FAKE_INSTALL_STATUS": "0" if install_ok else "1",
        "FAKE_CHECK_STATUS": "0",
        "FAKE_CALLS": str(calls),
        "FAKE_READY": str(tmp_path / "ready"),
        "FAKE_VENV": str(venv),
        "FAKE_PY_VERSION": "3.13.16",
        "FAKE_PYTHON": str(python),
    }
    return project, calls, env, poetry


def invoke(project, env, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(project), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def test_install_recovers_missing_imports_and_repeat_check_is_ready(tmp_path):
    project, calls, env, _poetry = fixture(tmp_path)
    missing = invoke(project, env)
    assert missing.returncode == 1
    assert "cannot import" in missing.stdout
    assert not calls.exists()
    installed = invoke(
        project,
        env,
        "--install",
        "--expected-base",
        BASE_SHA,
        "--expected-branch",
        "feature/test",
    )
    assert installed.returncode == 0
    assert "Poetry interpreter: 3.13.16" in installed.stdout
    assert "Requested Python pin: 3.13.13" in installed.stdout
    assert calls.read_text(encoding="utf-8") == "install\n"
    assert invoke(project, env).returncode == 0
    assert calls.read_text(encoding="utf-8") == "install\n"


def test_combined_make_install_dry_run_previews_without_mutation(tmp_path):
    project, calls, env, _poetry = fixture(tmp_path)
    make = shutil.which("make")
    assert make
    result = subprocess.run(
        [make, "worktree-ready", f"WORKTREE={project}", "INSTALL=1", "DRY_RUN=1"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 2  # GNU Make maps the helper's not-ready status to 2.
    assert "would run poetry install" in result.stdout
    assert not calls.exists()
    env["FAKE_IMPORTS"] = "1"
    ready = subprocess.run(
        [make, "worktree-ready", f"WORKTREE={project}", "INSTALL=1", "DRY_RUN=1"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert ready.returncode == 0
    assert not calls.exists()


def test_dirty_wrong_branch_and_unavailable_base_refuse_without_touching_files(
    tmp_path,
):
    for label, options, expected in (
        ("dirty", {"dirty": " M dirty.txt"}, "dirty.txt"),
        ("branch", {"branch": "wrong"}, "branch"),
        ("base", {"base_ok": False}, "base is unavailable"),
    ):
        project, calls, env, _poetry = fixture(tmp_path / label, **options)
        result = invoke(
            project,
            env,
            "--expected-base",
            BASE_SHA,
            "--expected-branch",
            "feature/test",
        )
        assert result.returncode == 1
        if label == "dirty":
            assert expected in result.stdout
            assert "Existing changes: dirty.txt" in result.stdout
        else:
            assert expected in result.stdout
        assert not calls.exists()
        assert (project / "dirty.txt").read_text(encoding="utf-8") == "preserve me\n"


def test_dry_run_and_failed_install_do_not_write_target_files(tmp_path):
    project, calls, env, _poetry = fixture(tmp_path, install_ok=False)
    dry_run = invoke(project, env, "--dry-run")
    assert dry_run.returncode == 1
    assert "cannot import" in dry_run.stdout
    preview = invoke(project, env, "--install", "--dry-run")
    assert preview.returncode == 1
    assert "would run poetry install" in preview.stdout
    assert not calls.exists()
    failure = invoke(project, env, "--install")
    assert failure.returncode == 1
    assert "dependency installation failed" in failure.stdout
    assert calls.read_text(encoding="utf-8") == "install\n"
    assert not (project / ".venv").exists()


def test_non_project_and_missing_poetry_fail_before_install(tmp_path):
    project, calls, env, poetry = fixture(tmp_path)
    (project / "pyproject.toml").write_text('[project]\nname = "not-dotfiles"\n')
    invalid = invoke(project, env, "--install")
    assert invalid.returncode == 1
    assert "not the dotfiles project" in invalid.stdout.lower()
    assert not calls.exists()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "dotfiles"\nversion = "0.1.0"\n'
        'requires-python = ">=3.10"\n',
        encoding="utf-8",
    )
    poetry.unlink()
    missing = invoke(project, env, "--install")
    assert missing.returncode == 1
    assert "Poetry executable is unavailable" in missing.stdout
    assert not calls.exists()


def test_poetry_patch_drift_warns_and_major_minor_drift_is_not_ready(tmp_path):
    project, _calls, env, _poetry = fixture(tmp_path / "patch", imports=True)
    patch_drift = invoke(project, env)
    assert patch_drift.returncode == 0
    assert "Requested Python pin: 3.13.13" in patch_drift.stdout
    assert "Poetry interpreter: 3.13.16" in patch_drift.stdout
    assert (
        "Warning: Poetry patch version 3.13.16 differs from pin 3.13.13"
        in patch_drift.stdout
    )

    other_project, _calls, other_env, _poetry = fixture(
        tmp_path / "minor", imports=True
    )
    other_env["FAKE_PY_VERSION"] = "3.14.8"
    minor_drift = invoke(other_project, other_env, "--install")
    assert minor_drift.returncode == 1
    assert (
        "Not ready: Poetry runtime family 3.14 differs from pin family 3.13"
        in minor_drift.stdout
    )
    assert "Ready: Git worktree" not in minor_drift.stdout
    assert not _calls.exists()
    other_env["FAKE_IMPORTS"] = "0"
    assert invoke(other_project, other_env, "--install").returncode == 1
    assert not _calls.exists()

    env["FAKE_PY_VERSION"] = "3.13.13"
    exact = invoke(project, env)
    assert exact.returncode == 0
    assert "Warning: Poetry patch version" not in exact.stdout


def test_poetry_rejects_runtime_below_minimum_and_malformed_requirement(tmp_path):
    project, calls, env, _poetry = fixture(tmp_path / "below", imports=True)
    env["FAKE_PY_VERSION"] = "3.9.13"
    below_floor = invoke(project, env, "--install")
    assert below_floor.returncode == 1
    assert "does not satisfy project requires-python '>=3.10'" in below_floor.stdout
    assert not calls.exists()

    malformed_project, malformed_calls, malformed_env, _poetry = fixture(
        tmp_path / "malformed", imports=True
    )
    pyproject = malformed_project / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "dotfiles"\nversion = "0.1.0"\n'
        'requires-python = "not a spec"\n',
        encoding="utf-8",
    )
    malformed = invoke(malformed_project, malformed_env, "--install")
    assert malformed.returncode == 1
    assert "malformed requires-python specifier" in malformed.stdout
    assert not malformed_calls.exists()


def test_poetry_missing_or_unparseable_interpreter_version_fails_read_only(
    tmp_path,
):
    for index, version in enumerate(("", "not-a-version")):
        project, calls, env, _poetry = fixture(tmp_path / str(index), imports=True)
        env["FAKE_PY_VERSION"] = version
        result = invoke(project, env)
        assert result.returncode == 1
        assert "unparseable python version" in result.stdout.lower()
        assert not calls.exists()


def test_subprocess_runner_applies_finite_timeout(monkeypatch):
    observed = {}

    def timeout(*_args, **kwargs):
        observed.update(kwargs)
        raise subprocess.TimeoutExpired("stub", kwargs["timeout"])

    monkeypatch.setattr(worktree_readiness.subprocess, "run", timeout)
    result = worktree_readiness.run(["stub"], timeout=7)
    assert isinstance(result, subprocess.TimeoutExpired)
    assert observed["timeout"] == 7
