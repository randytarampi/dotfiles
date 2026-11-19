#!/usr/bin/env python3
"""Read-only release drift inventory for the pinned runtime packages.

Compares the version pinned in the deploy templates against the latest upstream
release and reports the difference. It fetches metadata and prints a table; it
never edits a manifest, opens a pull request, or touches a runtime.

Deliberately read-only for the first phase. An updater that writes a rolling
draft PR is a separate, later change, and it should only exist after this
inventory has proven its value and its noise level.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# package -> (template, pin regex, upstream repo)
PACKAGES = {
    "litellm": (
        ".chezmoiscripts/run_onchange_15-litellm.sh.tmpl",
        re.compile(r"\$\{DOTFILES_LITELLM_VERSION:-([0-9][0-9A-Za-z.\-]*)\}"),
        "BerriAI/litellm",
    ),
    "open-webui": (
        ".chezmoiscripts/run_onchange_30-openwebui.sh.tmpl",
        re.compile(r"\$\{DOTFILES_OPENWEBUI_VERSION:-([0-9][0-9A-Za-z.\-]*)\}"),
        "open-webui/open-webui",
    ),
}

UNKNOWN = "UNKNOWN"


def pinned_version(template: Path, pattern: re.Pattern[str]) -> str:
    """Return the default version baked into a deploy template."""
    try:
        text = template.read_text(encoding="utf-8")
    except OSError:
        return UNKNOWN
    match = pattern.search(text)
    return match.group(1) if match else UNKNOWN


def _version_key(value: str) -> tuple:
    """Return a sortable key where a pre-release sorts *below* its release.

    1.2.0-rc1 must not read as newer than 1.2.0. Numeric release segments
    compare normally; a pre-release suffix is a tiebreaker that ranks lower
    than the absence of one.
    """
    core = value.lstrip("v").split("+", 1)[0]
    release, _, prerelease = core.partition("-")
    numbers = tuple(
        int(part) if part.isdigit() else 0 for part in release.split(".") if part
    )
    suffix = tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in re.split(r"[.\-]", prerelease)
        if part
    )
    return (numbers, 0 if prerelease else 1, suffix)


def classify(pinned: str, latest: str) -> str:
    """Return one of: CURRENT, BEHIND, AHEAD, UNKNOWN."""
    if UNKNOWN in (pinned, latest):
        return UNKNOWN
    try:
        pinned_key, latest_key = _version_key(pinned), _version_key(latest)
    except (TypeError, ValueError):
        return UNKNOWN
    if pinned_key == latest_key:
        return "CURRENT"
    return "BEHIND" if pinned_key < latest_key else "AHEAD"


def latest_release(repo: str, token: str | None, timeout: float = 15.0) -> str:
    """Return the latest tag, or UNKNOWN when the source is unreachable."""
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases/latest",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "dotfiles-release-inventory/1.0",
        },
    )
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return UNKNOWN
    tag = payload.get("tag_name")
    return tag.lstrip("v") if isinstance(tag, str) and tag else UNKNOWN


def build_rows(token: str | None) -> list[dict[str, str]]:
    rows = []
    for name, (template, pattern, repo) in PACKAGES.items():
        pinned = pinned_version(REPO_ROOT / template, pattern)
        latest = latest_release(repo, token)
        rows.append(
            {
                "package": name,
                "pinned": pinned,
                "latest": latest,
                "status": classify(pinned, latest),
                "source": f"https://github.com/{repo}/releases",
            }
        )
    return rows


def render(rows: list[dict[str, str]]) -> str:
    lines = [
        "| package | pinned | latest | status | source |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {package} | {pinned} | {latest} | {status} | {source} |".format(**row)
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--json", action="store_true", help="emit JSON, not a table")
    args = parser.parse_args()

    rows = build_rows(os.environ.get("GITHUB_TOKEN"))
    if args.json:
        print(json.dumps(rows, indent=2, sort_keys=True))
    else:
        print(render(rows))

    # A read-only inventory reports UNKNOWN rather than failing: an unreachable
    # upstream is not a repository defect, and a red job would train people to
    # ignore it. Only an unreadable template is a real problem.
    if any(row["pinned"] == UNKNOWN for row in rows):
        print("a pinned version could not be read from its template", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
