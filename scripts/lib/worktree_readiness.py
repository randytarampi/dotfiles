"""Read-only Git and Poetry probes shared by the worktree readiness CLI."""

import shutil
import subprocess
from pathlib import Path


def run(command, *, cwd=None, timeout=30):
    """Run a resolved tool command and capture failures without leaking output."""
    try:
        return subprocess.run(  # nosec B603 - internal commands, argv only, no shell.
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return error


def git_value(path, git_executable, *args):
    result = run([git_executable, "-C", str(path), *args])
    if isinstance(result, Exception) or result.returncode:
        return None
    return result.stdout.strip()


def is_timeout(result):
    return isinstance(result, subprocess.TimeoutExpired)


def _check_expected_base(path, git_executable, expected_base):
    if not expected_base:
        return None, None
    base = git_value(
        path, git_executable, "rev-parse", "--verify", f"{expected_base}^{{commit}}"
    )
    ancestor = run(
        [
            git_executable,
            "-C",
            str(path),
            "merge-base",
            "--is-ancestor",
            expected_base,
            "HEAD",
        ]
    )
    if base is None or isinstance(ancestor, Exception) or ancestor.returncode != 0:
        return (
            None,
            "Expected base is unavailable locally or is not an ancestor of HEAD.",
        )
    return base, None


def inspect_git_state(
    path, git_executable, expected_base, expected_branch, allow_dirty
):
    """Validate branch, optional base, and dirty state without changing files."""
    state = None
    error = None
    commit = git_value(path, git_executable, "rev-parse", "HEAD")
    branch = git_value(path, git_executable, "branch", "--show-current")
    status_result = run(
        [
            git_executable,
            "-C",
            str(path),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ]
    )
    status = (
        status_result.stdout
        if not isinstance(status_result, Exception) and status_result.returncode == 0
        else None
    )
    if commit is None or not branch or status is None:
        error = "Could not establish Git commit, branch and worktree status."
    else:
        dirty_paths = [line[3:] for line in status.splitlines() if line]
        state = {
            "commit": commit,
            "branch": branch,
            "dirty_paths": dirty_paths,
            "base": None,
        }
        if dirty_paths and not allow_dirty:
            error = "Worktree is dirty; inspect it or explicitly pass --allow-dirty."
        elif expected_branch and branch != expected_branch:
            error = "Checked-out branch does not match --expected-branch."
        else:
            base, error = _check_expected_base(path, git_executable, expected_base)
            if error is None:
                state["base"] = base
    return state, error


def validate_git_root(path, git_executable):
    root = git_value(path, git_executable, "rev-parse", "--show-toplevel")
    if root is None or Path(root).resolve() != path:
        return "Path is not the root of a Git worktree."
    return None
