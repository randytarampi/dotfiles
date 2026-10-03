"""Poetry metadata and interpreter readiness probes."""

import ast
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
                    "import sys; print(sys.version.split()[0]); print(sys.executable); import yaml, pytest, black",
                ],
                cwd=path,
            )
            if is_timeout(probe):
                error = "Timed out while probing Poetry interpreter imports."
            elif isinstance(probe, Exception):
                error = "Poetry interpreter cannot import required modules: yaml, pytest, black."
            else:
                lines = probe.stdout.splitlines()
                if len(lines) < 2:
                    error = (
                        "Poetry interpreter probe returned incomplete runtime details."
                    )
                else:
                    runtime = (lines[0], lines[1])
                    if probe.returncode != 0:
                        error = "Poetry interpreter cannot import required modules: yaml, pytest, black."
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
    requires_python = None
    if error is None:
        try:
            project_text = (path / "pyproject.toml").read_text(encoding="utf-8")
            lock_text = (path / "poetry.lock").read_text(encoding="utf-8")
        except OSError:
            error = "Poetry project metadata or lock file is malformed."
        else:
            project, project_error = _read_toml_section(
                project_text, "[project]", {"name", "requires-python"}
            )
            metadata, lock_error = _read_toml_section(
                lock_text, "[metadata]", {"lock-version"}
            )
            has_package = "[[package]]" in {
                line.strip() for line in lock_text.splitlines()
            }
            if project_error or lock_error:
                error = "Poetry project metadata or lock file is malformed."
            elif project.get("name") != "dotfiles" or not has_package:
                error = "Git checkout does not contain valid dotfiles Poetry project metadata."
            elif not isinstance(project.get("requires-python"), str):
                error = "pyproject.toml is missing the required requires-python string."
            else:
                requirement = project["requires-python"]
                try:
                    from packaging.specifiers import InvalidSpecifier, SpecifierSet

                    requires_python = SpecifierSet(requirement)
                except ImportError:
                    error = "Python requirement parser 'packaging' is unavailable."
                except InvalidSpecifier:
                    error = (
                        "pyproject.toml contains a malformed requires-python specifier."
                    )
    return error, requires_python


def _read_toml_section(source, section, wanted_keys):
    """Read selected single-line TOML string keys without regex parsing."""
    values = {}
    current_section = None
    error = None
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current_section = stripped
            continue
        if current_section != section:
            continue
        key, separator, raw_value = line.partition("=")
        if not separator or key.strip() not in wanted_keys:
            continue
        try:
            value = ast.literal_eval(raw_value.strip())
        except (SyntaxError, ValueError):
            error = f"Invalid TOML string for {section}.{key.strip()}."
            break
        if not isinstance(value, str):
            error = f"Expected a TOML string for {section}.{key.strip()}."
            break
        values[key.strip()] = value
    return values, error


def check_poetry_environment(
    path,
    poetry_executable,
    requires_python,
    *,
    requested_pin="unspecified",
    install=False,
    dry_run=False,
):
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
    if runtime is not None:
        compatibility_error = _runtime_compatibility_error(
            runtime[0], requires_python, requested_pin
        )
        if compatibility_error:
            return runtime, compatibility_error
    if runtime is not None and error is None:
        return runtime, error
    if dry_run or not install:
        return runtime, error
    if runtime is not None and not error.startswith("Poetry interpreter cannot import"):
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
    runtime, error = probe_poetry(path, poetry_executable)
    if runtime is not None:
        error = (
            _runtime_compatibility_error(runtime[0], requires_python, requested_pin)
            or error
        )
    return runtime, error


def _runtime_compatibility_error(actual, requires_python, requested_pin):
    """Check the project range and pinned family before any dependency install."""
    try:
        from packaging.version import InvalidVersion, Version
    except ImportError:
        return "Python requirement parser 'packaging' is unavailable."
    try:
        version = Version(actual)
        preferred = Version(requested_pin) if requested_pin != "unspecified" else None
    except InvalidVersion:
        return f"Unparseable Python version: Poetry reported {actual}, pin is {requested_pin}."
    error = None
    if not requires_python.contains(version, prereleases=True):
        error = f"Poetry interpreter {actual} does not satisfy project requires-python '{requires_python}'."
    elif preferred is not None and version.release[:2] != preferred.release[:2]:
        actual_family = ".".join(str(part) for part in version.release[:2])
        pin_family = ".".join(str(part) for part in preferred.release[:2])
        error = f"Poetry runtime family {actual_family} differs from pin family {pin_family}; select the pinned Python family for Poetry."
    return error
