#!/usr/bin/env python3
"""Read-only Linux Brewfile directive audit; never a package availability claim."""

import argparse
import json
from pathlib import Path
import re
import sys

ENTRY = re.compile(r'^(tap|brew|cask|npm|uv)\s+"([A-Za-z0-9@/_.+\-]+)"(?:\s*,\s*.+)?
# A macOS-specific name deserves a separate qualification, but is not
# declared impossible on Linux without actual endpoint/formula evidence.
MACOS_NAMED_CANDIDATES = frozenset(
    ("pinentry-mac", "carthage", "cocoapods", "ios-deploy", "wix/brew/applesimutils")
)


def inspect(path):
    """Return package-name-only inventory; never echo untrusted source lines."""
    file = Path(path)
    if not file.is_file() or file.is_symlink():
        raise ValueError(f"Missing or symlinked Brewfile: {file}")
    entries = []
    for line_no, raw in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        found = ENTRY.fullmatch(line)
        if found is None:
            entries.append(
                {"line": line_no, "kind": "invalid", "status": "blocked_syntax"}
            )
            continue
        kind, name = found.groups()
        if kind == "cask":
            status = "blocked_linux_cask"
        elif kind == "brew" and name in MACOS_NAMED_CANDIDATES:
            status = "macos_named_needs_evidence"
        elif kind in ("npm", "uv"):
            status = "external_runtime_not_qualified"
        else:
            status = "needs_linux_formula_or_tap_evidence"
        entries.append({"line": line_no, "kind": kind, "name": name, "status": status})
    return {"manifest": str(file), "entries": entries}


def assess(paths):
    documents = [inspect(path) for path in paths]
    statuses = [e["status"] for doc in documents for e in doc["entries"]]
    blocked = sum(s.startswith("blocked_") for s in statuses)
    return {
        "platform": "linux",
        "source": "static_manifest_only",
        "operational_verified": False,
        "can_install_without_known_blockers": blocked == 0,
        "packages_l1": "not_qualified",
        "blocked_entries": blocked,
        "manifests": documents,
        "notes": [
            "No formula/tap availability, version, licence or architecture was queried",
            "A zero-blocker result is NOT an install authorization or success receipt",
            "Brewfile selection must reflect actual chezmoi category values",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        action="append",
        type=Path,
        required=True,
        help="Selected Brewfile; repeat for each enabled Linux category",
    )
    parser.add_argument(
        "--json", action="store_true", help="Print machine-readable audit"
    )
    parser.add_argument(
        "--enforce",
        action="store_true",
        help="Fail before brew installation if casks/malformed entries were selected",
    )
    args = parser.parse_args(argv)
    try:
        result = assess(args.manifest)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"Linux Brewfile audit: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for doc in result["manifests"]:
            for entry in doc["entries"]:
                if entry["status"] != "needs_linux_formula_or_tap_evidence":
                    print(
                        f'{doc["manifest"]}:{entry["line"]}: '
                        f'{entry["kind"]} {entry.get("name", "<invalid>")} '
                        f'=> {entry["status"]}'
                    )
        print(
            f'Linux Brewfile static audit: {result["blocked_entries"]} blockers; '
            "remaining entries still require real Linux/CPU package qualification"
        )
    return 1 if args.enforce and result["blocked_entries"] else 0


if __name__ == "__main__":
    sys.exit(main())
)
# A macOS-specific name deserves a separate qualification, but is not
# declared impossible on Linux without actual endpoint/formula evidence.
MACOS_NAMED_CANDIDATES = frozenset(
    ("pinentry-mac", "carthage", "cocoapods", "ios-deploy", "wix/brew/applesimutils")
)


def inspect(path):
    """Return package-name-only inventory; never echo untrusted source lines."""
    file = Path(path)
    if not file.is_file() or file.is_symlink():
        raise ValueError(f"Missing or symlinked Brewfile: {file}")
    entries = []
    for line_no, raw in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        found = ENTRY.fullmatch(line)
        if found is None:
            entries.append(
                {"line": line_no, "kind": "invalid", "status": "blocked_syntax"}
            )
            continue
        kind, name = found.groups()
        if kind == "cask":
            status = "blocked_linux_cask"
        elif kind == "brew" and name in MACOS_NAMED_CANDIDATES:
            status = "macos_named_needs_evidence"
        elif kind in ("npm", "uv"):
            status = "external_runtime_not_qualified"
        else:
            status = "needs_linux_formula_or_tap_evidence"
        entries.append(
            {"line": line_no, "kind": kind, "name": name, "status": status}
        )
    return {"manifest": str(file), "entries": entries}


def assess(paths):
    documents = [inspect(path) for path in paths]
    statuses = [e["status"] for doc in documents for e in doc["entries"]]
    blocked = sum(s.startswith("blocked_") for s in statuses)
    return {
        "platform": "linux",
        "source": "static_manifest_only",
        "operational_verified": False,
        "can_install_without_known_blockers": blocked == 0,
        "packages_l1": "not_qualified",
        "blocked_entries": blocked,
        "manifests": documents,
        "notes": [
            "No formula/tap availability, version, licence or architecture was queried",
            "A zero-blocker result is NOT an install authorization or success receipt",
            "Brewfile selection must reflect actual chezmoi category values",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", action="append", type=Path, required=True,
        help="Selected Brewfile; repeat for each enabled Linux category"
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable audit")
    parser.add_argument(
        "--enforce", action="store_true",
        help="Fail before brew installation if casks/malformed entries were selected"
    )
    args = parser.parse_args(argv)
    try:
        result = assess(args.manifest)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"Linux Brewfile audit: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for doc in result["manifests"]:
            for entry in doc["entries"]:
                if entry["status"] != "needs_linux_formula_or_tap_evidence":
                    print(
                        f'{doc["manifest"]}:{entry["line"]}: '
                        f'{entry["kind"]} {entry.get("name", "<invalid>")} '
                        f'=> {entry["status"]}'
                    )
        print(
            f'Linux Brewfile static audit: {result["blocked_entries"]} blockers; '
            'remaining entries still require real Linux/CPU package qualification'
        )
    return 1 if args.enforce and result["blocked_entries"] else 0


if __name__ == "__main__":
    sys.exit(main())
