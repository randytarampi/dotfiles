#!/usr/bin/env python3
"""Check or prepare Python tooling in an existing dotfiles Git worktree."""

import argparse
import re
import shutil
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from worktree_readiness import (  # noqa: E402 -- import follows intentional lib bootstrap.
    inspect_git_state,
    validate_git_root,
)
from poetry_readiness import (  # noqa: E402 -- import follows intentional lib bootstrap.
    check_poetry_environment,
    validate_project,
)


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
    git_executable = shutil.which("git")
    if git_executable is None:
        print("Not ready: Git executable is unavailable on PATH.")
        return 1
    root_error = validate_git_root(path, git_executable)
    if root_error:
        print(f"Not ready: {root_error}")
        return 1
    project_error = validate_project(path, git_executable)
    if project_error:
        print("Not ready: Git checkout is not the dotfiles project.")
        print(project_error)
        return 1

    state, state_error = inspect_git_state(
        path,
        git_executable,
        args.expected_base,
        args.expected_branch,
        args.allow_dirty,
    )
    if state is None:
        print(f"Not ready: {state_error}")
        return 1
    print(f"Worktree: {path}\nBranch: {state['branch']}\nHEAD: {state['commit']}")
    dirty_paths = state["dirty_paths"]
    print("Existing changes: " + (", ".join(dirty_paths) if dirty_paths else "none"))
    if state_error:
        print(f"Not ready: {state_error}")
        return 1
    if state["base"]:
        print(f"Expected base: {state['base']} (ancestor verified locally)")

    pin_path = path / ".python-version"
    requested_pin = (
        pin_path.read_text(encoding="utf-8").strip()
        if pin_path.is_file()
        else "unspecified"
    )
    print(f"Requested Python pin: {requested_pin}")
    poetry_executable = shutil.which("poetry")
    if poetry_executable is None:
        print("Not ready: Poetry executable is unavailable on PATH.")
        return 1
    if args.install and not args.dry_run:
        print("Installing Poetry groups: --no-root --with tooling,test")
    runtime, environment_error = check_poetry_environment(
        path,
        poetry_executable,
        install=args.install,
        dry_run=args.dry_run,
    )
    if runtime is None:
        if args.dry_run and args.install and environment_error:
            print(
                f"{environment_error} Dry run: would run poetry install "
                "--no-root --with tooling,test."
            )
        else:
            print(f"Not ready: {environment_error}")
        return 1
    print(f"Poetry interpreter: {runtime[0]} ({runtime[1]})")
    print("Ready: Git worktree and Poetry tooling checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
