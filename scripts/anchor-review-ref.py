#!/usr/bin/env python3
"""Update the local review dispatcher's trusted_ref to reachable origin/main."""

import argparse
import os
import re
import shutil
import subprocess  # nosec B404 - fixed-argument git invocations only.
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DISPATCHER = ROOT / ".github/workflows/agent-review.yml"
COPILOT_SETUP = ROOT / ".github/workflows/copilot-setup-steps.yml"
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

from cli_helpers import add_common_args  # noqa: E402 -- repository library bootstrap.


def git(*args):
    git_executable = shutil.which("git")
    if git_executable is None:
        raise FileNotFoundError("git executable is unavailable on PATH")
    return subprocess.run(
        [
            git_executable,
            *args,
        ],  # nosec B603 - PATH-resolved git, fixed args, no shell.
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def atomic_write(path, content):
    """Replace a file atomically, retaining its current mode when present."""
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o666
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    add_common_args(parser)
    args = parser.parse_args()
    try:
        branch = git("branch", "--show-current")
        if branch != "main":
            raise ValueError(
                f"must run on main (current branch: {branch or 'detached HEAD'})"
            )
        if args.dry_run:
            remote = git("ls-remote", "origin", "refs/heads/main")
            sha = _remote_sha(remote)
        else:
            git(
                "fetch",
                "--no-tags",
                "origin",
                "+refs/heads/main:refs/remotes/origin/main",
            )
            sha = git("rev-parse", "--verify", "FETCH_HEAD^{commit}")
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError(
                "origin/main did not resolve to a full 40-character commit SHA"
            )
        if args.dry_run:
            try:
                git("cat-file", "-e", f"{sha}^{{commit}}")
            except subprocess.CalledProcessError as exc:
                raise ValueError(
                    "origin/main commit is not available locally; run without --dry-run to fetch it"
                ) from exc
        try:
            git("merge-base", "--is-ancestor", sha, "HEAD")
        except subprocess.CalledProcessError as exc:
            raise ValueError(
                "origin/main commit is not reachable from local main"
            ) from exc
        content = DISPATCHER.read_text(encoding="utf-8")
        if not re.search(
            r"(?m)^\s*uses:\s*randytarampi/dotfiles/\.github/workflows/agentic-review\.yml@main\s*$",
            content,
        ):
            raise ValueError("dispatcher must use the reusable workflow at @main")
        matches = list(
            re.finditer(
                r"(?m)^(\s*trusted_ref:\s*)[0-9a-f]{40}(\s*(?:#.*)?)$",
                content,
            )
        )
        if len(matches) != 1:
            raise ValueError(
                "dispatcher must contain exactly one SHA-valued trusted_ref"
            )
        copilot_content = COPILOT_SETUP.read_text(encoding="utf-8")
        copilot_matches = list(
            re.finditer(
                r"(?m)^[ \t]*default:[ \t]*([0-9a-f]{40})[ \t]*$",
                copilot_content,
            )
        )
        if len(copilot_matches) != 1:
            raise ValueError(
                "Copilot setup must contain exactly one SHA-valued trusted_ref default"
            )
        copilot_sha = copilot_matches[0].group(1)
        if copilot_content.count(copilot_sha) != 2:
            raise ValueError(
                "Copilot setup must use the default trusted_ref for automatic runs"
            )
        updated = re.sub(
            r"(?m)^(\s*trusted_ref:\s*)[0-9a-f]{40}(\s*(?:#.*)?)$",
            lambda match: f"{match.group(1)}{sha}{match.group(2)}",
            content,
        )
        updated_copilot = copilot_content.replace(copilot_sha, sha)
        if updated == content and updated_copilot == copilot_content:
            print(f"trusted_ref already anchored to {sha}")
        elif args.dry_run:
            print(
                f"[DRY RUN] Would set trusted_ref to {sha} in {DISPATCHER} and {COPILOT_SETUP}"
            )
        else:
            if updated != content:
                atomic_write(DISPATCHER, updated)
            if updated_copilot != copilot_content:
                atomic_write(COPILOT_SETUP, updated_copilot)
            print(f"Updated trusted refs to {sha} in {DISPATCHER} and {COPILOT_SETUP}")
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"anchor-review-ref: {exc}", file=sys.stderr)
        return 1
    return 0


def _remote_sha(remote):
    """Return exactly one main-branch SHA from ls-remote output."""
    entries = [line.split() for line in remote.splitlines() if line.strip()]
    if len(entries) != 1 or len(entries[0]) != 2:
        return ""
    sha, ref = entries[0]
    return sha if ref == "refs/heads/main" else ""


if __name__ == "__main__":
    raise SystemExit(main())
