"""Resolve the installed OpenCode host version for native v2 emission gates.

The one-way migration emits native OpenCode v2 configuration. A machine
still running an OpenCode v1 binary cannot consume those keys, so emitters
probe the installed host and skip with a loud warning instead of handing
v2 keys to a v1 binary (which would silently lose providers, plugins and
permissions from the consumer's view). Policy:

- binary absent          -> emit/allow (fresh or dev machine; nothing to break)
- installed major == 2   -> emit/allow
- installed major != 2   -> skip with warning (machine keeps its last honest
                            v1-shaped config until the cutover runs)
- present but unprobeable -> skip with warning (missing evidence never
                            substitutes for success)

Legacy v1 permission maps stay functional even on v2 hosts (upstream
normalizes supported V1 syntax silently), so the skill-permission writer
uses the conservative legacy shape whenever the host is not provably v2.

Tests override ``resolve_opencode_host`` (see conftest's autouse fixture).
"""

from __future__ import annotations

import re
import shutil
import subprocess  # nosec B404 - fixed "--version" probe of a shutil.which-resolved binary.

_VERSION_RE = re.compile(r"(?<![A-Za-z0-9.+-])v?(\d+\.\d+\.\d+)(?![A-Za-z0-9.+-])")


def _probe_host(binary: str) -> tuple[str, int | None, str | None]:
    """Implementation of the host probe; public names stay patchable seams.

    Returns (state, major, version-string) where the version string is the
    exact matched stable ``X.Y.Z`` token, when found."""
    resolved = shutil.which(binary)
    if resolved is None:
        return ("absent", None, None)
    try:
        probe = subprocess.run(  # nosec B603 - absolute binary from shutil.which; literal args.
            [resolved, "--version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ("unknown", None, None)
    if probe.returncode != 0:
        return ("unknown", None, f"probe exited {probe.returncode}")
    versions = set()
    for stream in (probe.stdout or "", probe.stderr or ""):
        versions.update(match.group(1) for match in _VERSION_RE.finditer(stream))
    if len(versions) == 1:
        version = versions.pop()
        return ("known", int(version.split(".", 1)[0]), version)
    return ("unknown", None, None)


def resolve_opencode_host(binary: str = "opencode") -> tuple[str, int | None]:
    """Return (state, major): ("absent", None), ("unknown", None) or
    ("known", <major>). Tests monkeypatch this seam; see conftest's
    autouse ``assume_v2_opencode_host`` fixture."""
    state, major, _version = _probe_host(binary)
    return (state, major)


def host_version_string(binary: str = "opencode") -> str | None:
    """Return the probed host's exact version token (``X.Y.Z``) or None."""
    _state, _major, version = _probe_host(binary)
    return version


def native_emission_decision(state: str, major: int | None, logger=None) -> bool:
    """Decide whether native v2 config emission may proceed for this host.

    Warns through a single aggregated logging call when emission is skipped
    or when emitting against an unprobeable host, and returns False in
    those cases. ``("absent", None)`` is allowed with an informational
    note so fresh installs are not blocked.
    """

    def _note(lines):
        if logger is not None:
            logger.warning("\n".join(lines))

    if state == "absent":
        _note(
            [
                "No OpenCode binary found on PATH — emitting native v2 "
                "configuration for the upcoming install."
            ]
        )
        return True
    if state == "unknown":
        _note(
            [
                "OpenCode binary is present but its version could not be "
                "probed — refusing to emit native v2 configuration without "
                "host evidence. Skipping OpenCode configuration for this "
                "run (machine config left unchanged)."
            ]
        )
        return False
    if major == 2:
        return True
    _note(
        [
            f"Installed OpenCode host is v{major} — native v2 configuration "
            "requires an OpenCode 2.x host. Skipping OpenCode configuration "
            "for this run (machine config left unchanged). Complete the v2 "
            "machine-cutover steps before re-running OpenCode configuration."
        ]
    )
    return False
