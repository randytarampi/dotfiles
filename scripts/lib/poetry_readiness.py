"""Poetry metadata and interpreter readiness probes."""

import re
from pathlib import Path

from worktree_readiness import is_timeout, run


def probe_poetry(path, poetry_executable):
    """Return the Poetry interpreter's actual runtime or a safe failure."""
    runtime = None
    error = None
    environment = run([poetry_executable, "env", "info", "--path"], cwd=path)
    if is_timeout(environment):
        error = "Timed out while locating the Poetry environment."
    elif isinstance(environment, Exception) or environment.returncode != 0:
        error = "Could not locate the Poetry environment; run explicit install mode."
    else:
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
            error = "Poetry reported an environment without a Python interpreter."
        else:
            probe = run(
                [
                    str(interpreter),
                    "-c",
                    "import sys, yaml, pytest, black; print(sys.version.split()[0]); print(sys.executable)",
                ],
                cwd=path,
            )
            if is_timeout(probe):
                error = "Timed out while probing Poetry interpreter imports."
            elif isinstance(probe, Exception) or probe.returncode != 0:
                error = "Poetry interpreter cannot import required modules: yaml, pytest, black."
            else:
                lines = probe.stdout.splitlines()
                if len(lines) < 2:
                    error = (
                        "Poetry interpreter probe returned incomplete runtime details."
                    )
                else:
                    runtime = (lines[0], lines[1])
    return runtime, error


def _validate_tracked_metadata(path, git_executable):
    error = None
    for filename in ("pyproject.toml", "poetry.lock"):
        if not (path / filename).is_file():
            error = f"Required project file is missing: {filename}."
            break
        tracked = run(
            [git_executable, "-C", str(path), "ls-files", "--error-unmatch", filename]
        )
        if is_timeout(tracked):
            error = f"Timed out checking tracked project metadata: {filename}."
            break
        if isinstance(tracked, Exception) or tracked.returncode != 0:
            error = f"Project metadata is not tracked by Git: {filename}."
            break
    return error


def validate_project(path, git_executable):
    """Require tracked Poetry metadata and this repository's project identity."""
    error = _validate_tracked_metadata(path, git_executable)
    if error is None:
        try:
            project_text = (path / "pyproject.toml").read_text(encoding="utf-8")
            lock_text = (path / "poetry.lock").read_text(encoding="utf-8")
        except OSError:
            error = "Poetry project metadata or lock file is malformed."
        else:
            project_section = re.search(
                r"(?ms)^\[project\]\s*(.*?)(?=^\[|\Z)", project_text
            )
            has_dotfiles_project = project_section and re.search(
                r'(?m)^name\s*=\s*["\']dotfiles["\']\s*$',
                project_section.group(1),
            )
            valid_lock = re.search(r"(?m)^\[metadata\]\s*$", lock_text)
            valid_lock = valid_lock and re.search(
                r'(?m)^lock-version\s*=\s*["\']\d', lock_text
            )
            valid_lock = valid_lock and re.search(
                r"(?m)^\[\[package\]\]\s*$", lock_text
            )
            if not has_dotfiles_project or not valid_lock:
                error = "Git checkout does not contain valid dotfiles Poetry project metadata."
    return error


def check_poetry_environment(path, poetry_executable, *, install=False, dry_run=False):
    """Validate Poetry metadata/imports, optionally installing declared groups."""
    metadata = run([poetry_executable, "check", "--lock"], cwd=path, timeout=60)
    if is_timeout(metadata):
        return None, "Timed out validating pyproject.toml and poetry.lock."
    if isinstance(metadata, Exception) or metadata.returncode != 0:
        status = (
            f"exit status {metadata.returncode}"
            if not isinstance(metadata, Exception)
            else "command could not run"
        )
        return None, f"Poetry rejected project metadata or lock ({status})."
    runtime, error = probe_poetry(path, poetry_executable)
    if runtime is not None or dry_run:
        return runtime, error
    if not install:
        return runtime, error
    result = run(
        [poetry_executable, "install", "--no-root", "--with", "tooling,test"],
        cwd=path,
        timeout=600,
    )
    if is_timeout(result):
        return None, "Poetry dependency installation timed out after 600 seconds."
    if isinstance(result, Exception) or result.returncode != 0:
        status = (
            f"exit status {result.returncode}"
            if not isinstance(result, Exception)
            else "command could not run"
        )
        return None, f"Poetry dependency installation failed ({status})."
    return probe_poetry(path, poetry_executable)
