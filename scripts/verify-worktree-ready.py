#!/usr/bin/env python3
"""Check or prepare Python tooling in an existing dotfiles Git worktree."""

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def run(command, *, cwd=None, timeout=30):
    """Run a command without surfacing arbitrary child output or environment."""
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return error


def git_value(path, *args):
    result = run(["git", "-C", str(path), *args])
    if isinstance(result, Exception) or result.returncode:
        return None
    return result.stdout.strip()


def probe_poetry(path):
    """Return Poetry interpreter facts or a safe stage-specific failure."""
    environment = run(["poetry", "env", "info", "--path"], cwd=path)
    if isinstance(environment, subprocess.TimeoutExpired):
        return None, "Timed out while locating the Poetry environment."
    if isinstance(environment, Exception) or environment.returncode != 0:
        return (
            None,
            "Could not locate the Poetry environment; run explicit install mode.",
        )
    venv = Path(environment.stdout.strip())
    candidates = (
        venv / "bin/python",
        venv / "bin/python3",
        venv / "Scripts/python.exe",
    )
    interpreter = next(
        (candidate for candidate in candidates if candidate.is_file()), None
    )
    if interpreter is None:
        return None, "Poetry reported an environment without a Python interpreter."
    result = run(
        [
            str(interpreter),
            "-c",
            "import sys, yaml, pytest, black; print(sys.version.split()[0]); print(sys.executable)",
        ],
        cwd=path,
    )
    if isinstance(result, subprocess.TimeoutExpired):
        return None, "Timed out while probing Poetry interpreter imports."
    if isinstance(result, Exception) or result.returncode != 0:
        return (
            None,
            "Poetry interpreter cannot import required modules: yaml, pytest, black.",
        )
    lines = result.stdout.splitlines()
    if len(lines) < 2:
        return None, "Poetry interpreter probe returned incomplete runtime details."
    return (lines[0], lines[1]), None


def validate_project(path):
    """Require tracked Poetry metadata and this repository's project identity."""
    for filename in ("pyproject.toml", "poetry.lock"):
        if not (path / filename).is_file():
            return f"Required project file is missing: {filename}."
        tracked = run(["git", "-C", str(path), "ls-files", "--error-unmatch", filename])
        if isinstance(tracked, subprocess.TimeoutExpired):
            return f"Timed out checking tracked project metadata: {filename}."
        if isinstance(tracked, Exception) or tracked.returncode != 0:
            return f"Project metadata is not tracked by Git: {filename}."
    try:
        project_text = (path / "pyproject.toml").read_text(encoding="utf-8")
        lock_text = (path / "poetry.lock").read_text(encoding="utf-8")
    except OSError:
        return "Poetry project metadata or lock file is malformed."
    project_section = re.search(r"(?ms)^\[project\]\s*(.*?)(?=^\[|\Z)", project_text)
    if (
        not project_section
        or not re.search(
            r'(?m)^name\s*=\s*["\']dotfiles["\']\s*$', project_section.group(1)
        )
        or not re.search(r"(?m)^\[metadata\]\s*$", lock_text)
        or not re.search(r'(?m)^lock-version\s*=\s*["\']\d', lock_text)
        or not re.search(r"(?m)^\[\[package\]\]\s*$", lock_text)
    ):
        return "Git checkout does not contain valid dotfiles Poetry project metadata."
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Check Poetry tooling readiness in an existing dotfiles worktree",
        allow_abbrev=False,
    )
    parser.add_argument("worktree", help="Existing dotfiles worktree path")
    parser.add_argument(
        "--expected-base", help="Full local base commit SHA required as an ancestor"
    )
    parser.add_argument(
        "--expected-branch", help="Require this checked-out branch name"
    )
    parser.add_argument(
        "--allow-dirty", action="store_true", help="Report but allow existing changes"
    )
    parser.add_argument(
        "--install", action="store_true", help="Install Poetry tooling and test groups"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Report planned install without mutation"
    )
    args = parser.parse_args(argv)
    if args.expected_base and not re.fullmatch(
        r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", args.expected_base
    ):
        parser.error("--expected-base must be a full hexadecimal commit SHA")

    path = Path(args.worktree).expanduser().resolve()
    if not path.is_dir():
        print("Not ready: worktree path does not exist or is not a directory.")
        return 1
    root = git_value(path, "rev-parse", "--show-toplevel")
    if root is None or Path(root).resolve() != path:
        print("Not ready: path is not the root of a Git worktree.")
        return 1
    project_problem = validate_project(path)
    if project_problem:
        print("Not ready: Git checkout is not the dotfiles project.")
        print(project_problem)
        return 1

    commit = git_value(path, "rev-parse", "HEAD")
    branch = git_value(path, "branch", "--show-current")
    status_result = run(
        ["git", "-C", str(path), "status", "--porcelain=v1", "--untracked-files=all"]
    )
    status = (
        status_result.stdout
        if not isinstance(status_result, Exception) and status_result.returncode == 0
        else None
    )
    if commit is None or not branch or status is None:
        print("Not ready: could not establish Git commit, branch and worktree status.")
        return 1
    dirty_paths = [line[3:] for line in status.splitlines() if line]
    print(f"Worktree: {path}\nBranch: {branch}\nHEAD: {commit}")
    print("Existing changes: " + (", ".join(dirty_paths) if dirty_paths else "none"))
    if dirty_paths and not args.allow_dirty:
        print(
            "Not ready: worktree is dirty; inspect it or explicitly pass --allow-dirty."
        )
        return 1
    if args.expected_branch and branch != args.expected_branch:
        print("Not ready: checked-out branch does not match --expected-branch.")
        return 1
    if args.expected_base:
        base = git_value(
            path, "rev-parse", "--verify", f"{args.expected_base}^{{commit}}"
        )
        ancestor = run(
            [
                "git",
                "-C",
                str(path),
                "merge-base",
                "--is-ancestor",
                args.expected_base,
                "HEAD",
            ]
        )
        if base is None or isinstance(ancestor, Exception) or ancestor.returncode != 0:
            print(
                "Not ready: expected base is unavailable locally or is not an ancestor of HEAD."
            )
            return 1
        print(f"Expected base: {base} (ancestor verified locally)")

    pin_path = path / ".python-version"
    requested_pin = (
        pin_path.read_text(encoding="utf-8").strip()
        if pin_path.is_file()
        else "unspecified"
    )
    print(f"Requested Python pin: {requested_pin}")
    if not shutil.which("poetry"):
        print("Not ready: Poetry executable is unavailable on PATH.")
        return 1
    metadata = run(["poetry", "check", "--lock"], cwd=path, timeout=60)
    if isinstance(metadata, subprocess.TimeoutExpired):
        print("Not ready: timed out validating pyproject.toml and poetry.lock.")
        return 1
    if isinstance(metadata, Exception) or metadata.returncode != 0:
        status = (
            f"exit status {metadata.returncode}"
            if not isinstance(metadata, Exception)
            else "command could not run"
        )
        print(
            "Not ready: Poetry rejected project metadata or lock "
            f"({status}); inspect pyproject.toml and poetry.lock."
        )
        return 1
    runtime, probe_error = probe_poetry(path)
    if runtime is None and args.dry_run:
        print(
            f"{probe_error} Dry run: would run poetry install --no-root --with tooling,test."
        )
        return 1
    if runtime is None and args.install:
        print("Installing Poetry groups: --no-root --with tooling,test")
        install = run(
            ["poetry", "install", "--no-root", "--with", "tooling,test"],
            cwd=path,
            timeout=600,
        )
        if isinstance(install, subprocess.TimeoutExpired):
            print(
                "Not ready: Poetry dependency installation timed out after 600 seconds."
            )
            return 1
        if isinstance(install, Exception) or install.returncode != 0:
            status = (
                f"exit status {install.returncode}"
                if not isinstance(install, Exception)
                else "command could not run"
            )
            print(
                "Not ready: Poetry dependency installation failed "
                f"({status}); check local Poetry diagnostics."
            )
            return 1
        runtime, probe_error = probe_poetry(path)
    if runtime is None:
        print(f"Not ready: {probe_error}")
        return 1
    print(f"Poetry interpreter: {runtime[0]} ({runtime[1]})")
    print("Ready: Git worktree and Poetry tooling checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
