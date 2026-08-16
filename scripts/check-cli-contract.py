#!/usr/bin/env python3
"""Check script command-line interfaces against the CLI capability manifest.

Reads scripts/lib/cli-contract.json and validates:
  - Manifest schema (non-empty, entries have required fields)
  - Capability → required flag derivation (from common_flags.required_for_capabilities)
  - ``--help`` smoke test on ALL entries (side-effect-free by contract)
  - Invalid flag rejection test on ``strict`` entries only
  - ``pending`` entries reported as audit debt (``--help`` tested, invalid-flag skipped)

Exit 0: manifest is consistent and all smoke tests passed.
Exit 1: violations found.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "scripts" / "lib" / "cli-contract.json"

REQUIRED_ENTRY_FIELDS = {"path", "capabilities", "accepted_flags", "enforcement"}
VALID_ENFORCEMENT = {"pending", "strict"}
HELP_MARKERS = ("usage", "options", "flags", "arguments")
INVALID_FLAG = "--__cli_contract_invalid_flag__"


def load_manifest():
    """Load and parse the CLI contract manifest."""
    if not MANIFEST.exists():
        print(f"✗ CLI contract manifest not found: {MANIFEST}")
        sys.exit(1)
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"✗ Could not read CLI contract manifest: {error}")
        sys.exit(1)


def get_capabilities(entry):
    """Return an entry's capabilities as a set."""
    value = entry.get("capabilities", [])
    return set(value or []) if isinstance(value, list) else set()


def derive_required_flags(manifest, caps):
    """Derive required flags from common_flags.required_for_capabilities."""
    required = set()
    for _key, flag_spec in manifest.get("common_flags", {}).items():
        flag = flag_spec.get("flag")
        req_caps = set(flag_spec.get("required_for_capabilities", []))
        if caps & req_caps and flag:
            required.add(flag)
    return required


def safe_env():
    """Build a sanitized environment for probe execution."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": tempfile.mkdtemp(prefix="cli-contract-"),
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
        "TERM": "dumb",
        "NO_COLOR": "1",
    }
    return env


def run_probe(path, args):
    """Run a script with args in a sanitized environment. Returns subprocess result or Exception."""
    if path.suffix == ".py":
        command = [sys.executable, str(path)] + args
    else:
        command = ["bash", str(path)] + args
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(REPO_ROOT),
            env=safe_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return error


def validate_manifest(manifest):
    """Validate manifest structure and return violations."""
    violations = []

    if not isinstance(manifest, dict):
        return ["manifest must be an object"]
    entries = manifest.get("scripts", [])
    if not isinstance(entries, list) or not entries:
        violations.append("manifest has no script entries")
        return violations

    common_flags = manifest.get("common_flags", {})
    if not common_flags:
        violations.append("manifest has no common_flags section")

    for entry in entries:
        if not isinstance(entry, dict):
            violations.append(f"invalid manifest entry: {entry!r}")
            continue

        label = entry.get("path", "<missing path>")

        for field in REQUIRED_ENTRY_FIELDS:
            if field not in entry:
                violations.append(f"{label}: missing required field '{field}'")

        enforcement = entry.get("enforcement")
        if enforcement not in VALID_ENFORCEMENT:
            violations.append(
                f"{label}: invalid enforcement '{enforcement}' (must be 'pending' or 'strict')"
            )

        caps = get_capabilities(entry)
        if not isinstance(entry.get("capabilities"), list):
            violations.append(f"{label}: capabilities must be a list")
        if not isinstance(entry.get("accepted_flags"), list):
            violations.append(f"{label}: accepted_flags must be a list")

        # Missing-flag checks: violations for strict, audit debt for pending
        enforcement = entry.get("enforcement", "pending")
        required = derive_required_flags(manifest, caps)
        accepted = set(entry.get("accepted_flags", []))
        missing_flags = required - accepted
        if missing_flags and enforcement == "strict":
            for flag in sorted(missing_flags):
                violations.append(
                    f"{label}: missing required flag {flag} for capabilities {caps}"
                )

        if "orchestrates" in caps and not entry.get("child_scripts"):
            violations.append(
                f"{label}: orchestrates capability requires child_scripts"
            )

        path = REPO_ROOT / label if label != "<missing path>" else None
        if path and not path.exists():
            violations.append(f"{label}: script does not exist")

    return violations


def smoke_test_help(entry):
    """Run --help smoke test on any entry (side-effect-free by contract).

    Returns list of violations.
    """
    label = entry["path"]
    path = REPO_ROOT / label
    violations = []

    if not path.exists():
        return [f"{label}: script does not exist"]

    accepted = set(entry.get("accepted_flags", []))
    if "--help" not in accepted:
        violations.append(f"{label}: public script must list --help in accepted_flags")
        return violations

    result = run_probe(path, ["--help"])
    if isinstance(result, Exception):
        violations.append(f"{label}: --help could not run ({result})")
    elif result.returncode != 0:
        violations.append(f"{label}: --help exited {result.returncode}")
    elif not result.stdout.strip():
        violations.append(f"{label}: --help produced empty output")
    elif not any(
        marker in (result.stdout + result.stderr).lower() for marker in HELP_MARKERS
    ):
        violations.append(f"{label}: --help output missing usage/options marker")

    return violations


def smoke_test_invalid_flag(entry):
    """Run invalid-flag rejection test on a strict entry.

    Returns list of violations.
    """
    label = entry["path"]
    path = REPO_ROOT / label
    violations = []

    invalid_result = run_probe(path, [INVALID_FLAG])
    if isinstance(invalid_result, Exception):
        violations.append(
            f"{label}: invalid-flag test could not run ({invalid_result})"
        )
    elif invalid_result.returncode == 0:
        violations.append(f"{label}: accepts invalid flag (should exit 2)")
    elif invalid_result.returncode != 2:
        violations.append(
            f"{label}: invalid flag exits {invalid_result.returncode} (should exit 2)"
        )

    return violations


def main():
    manifest = load_manifest()
    entries = manifest.get("scripts", [])

    violations = validate_manifest(manifest)

    strict_count = 0
    pending_count = 0
    pending_entries = []

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        enforcement = entry.get("enforcement", "pending")
        label = entry.get("path", "<missing>")

        # --help smoke test runs on ALL entries (side-effect-free by contract).
        violations.extend(smoke_test_help(entry))

        if enforcement == "strict":
            strict_count += 1
            # Invalid-flag rejection test runs on strict entries only.
            violations.extend(smoke_test_invalid_flag(entry))
        else:
            pending_count += 1
            pending_entries.append(label)

    print("CLI contract report")
    print("=" * 60)
    print(
        f"Entries: {len(entries)} total, {strict_count} strict, {pending_count} pending"
    )

    if pending_entries:
        print(
            f"\nAudit debt ({pending_count} pending entries — --help tested, invalid-flag test skipped):"
        )
        for label in sorted(pending_entries):
            print(f"  ○ {label}")

    if violations:
        print(f"\n{len(violations)} violation(s):")
        for violation in violations:
            print(f"  ✗ {violation}")
        return 1

    print(
        f"\n✓ All {strict_count} strict entries conform. {pending_count} pending (audit debt)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
