#!/usr/bin/env python3
"""Collect privacy-bounded bootstrap acceptance receipts without running setup."""

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

STAGES = ("preflight", "first_install", "second_install", "deploy", "verify")
OUTCOMES = {"pass", "fail", "not_run", "not_applicable", "unknown"}
TOOLS = ("git", "python3", "python", "make", "brew", "winget", "chezmoi")
SECRET_KEY = re.compile(r"key|token|secret|password|authorization", re.I)
SHA = re.compile(r"^[0-9a-f]{40,64}$")


class ReceiptError(ValueError):
    pass


def safe_text(value):
    return value if isinstance(value, str) and value else "unknown"


def path_class(path):
    if not path:
        return "unknown"
    value = str(path).replace("\\", "/").lower()
    if "/.nvm/" in value:
        return "nvm"
    if "/homebrew/" in value or value.startswith("/opt/homebrew/"):
        return "homebrew"
    if "/toolcache/" in value or "\\_work\\_tool\\" in str(path).lower():
        return "runner-toolcache"
    if value.startswith(("/usr/", "c:/windows/", "c:/program files/")):
        return "system"
    return "other"


VERSION_TIMEOUT_SECONDS = 5
VERSION_MAX_CHARS = 120


def _command_output(command):
    """Run a fixed, read-only command and return its trimmed output.

    The executable is resolved through ``shutil.which`` so the invocation uses
    an absolute path, and no shell is involved. A missing or failing tool is not
    an error; callers record ``unknown``.
    """
    resolved = shutil.which(command[0])
    if not resolved:
        return None
    try:
        result = subprocess.run(
            [resolved, *command[1:]],
            capture_output=True,
            text=True,
            timeout=VERSION_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return (result.stdout or result.stderr or "").strip()


def _linux_facts():
    os_id = os_version = "unknown"
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            key, sep, value = line.partition("=")
            if not sep:
                continue
            if key == "ID":
                os_id = value.strip("\"'")
            elif key == "VERSION_ID":
                os_version = value.strip("\"'")
    except OSError:
        pass
    return os_id, os_version, "unknown"


def _darwin_facts():
    os_version = os_build = "unknown"
    data = _command_output(["sw_vers"]) or ""
    for line in data.splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "ProductVersion":
            os_version = value.strip()
        elif key.strip() == "BuildVersion":
            os_build = value.strip()
    return "macos", os_version, os_build


def _cpu_model(system):
    if system == "Darwin":
        return (
            _command_output(["sysctl", "-n", "machdep.cpu.brand_string"]) or "unknown"
        )
    if system == "Linux":
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.lower().startswith(("model name", "hardware")):
                    return line.split(":", 1)[-1].strip() or "unknown"
        except OSError:
            return "unknown"
    return "unknown"


def host_facts():
    system = platform.system()
    if system == "Linux":
        os_id, os_version, os_build = _linux_facts()
    elif system == "Darwin":
        os_id, os_version, os_build = _darwin_facts()
    elif system == "Windows":
        os_id, os_version, os_build = (
            "windows",
            safe_text(platform.version()),
            "unknown",
        )
    else:
        os_id = os_version = os_build = "unknown"
    return {
        "os_id": os_id,
        "os_version": os_version,
        "os_build": os_build,
        "kernel": (
            safe_text(platform.release())
            if system in ("Linux", "Darwin", "Windows")
            else "unknown"
        ),
        "architecture": safe_text(platform.machine()),
        "cpu_model": _cpu_model(system),
    }


VERSION_MAX_CHARS = 120


def tool_version(name):
    """Return the tool's first version line, or ``unknown``.

    Only ``--version`` is ever executed. No installer, service manager, sudo,
    deploy or hypervisor is invoked, and a probe failure is never an error.
    """
    output = _command_output([name, "--version"]) or ""
    lines = output.splitlines()
    if not lines:
        return "unknown"
    return lines[0][:VERSION_MAX_CHARS] or "unknown"


def executable_type(path):
    """Classify the executable container from its magic bytes."""
    if not path:
        return "unknown"
    try:
        with open(path, "rb") as handle:
            head = handle.read(4)
    except OSError:
        return "unknown"
    if head.startswith(b"#!"):
        return "script"
    if head.startswith(b"\x7fELF"):
        return "elf"
    if head.startswith(b"MZ"):
        return "pe"
    if head[:2] in (b"\xcf\xfa", b"\xce\xfa") or head == b"\xca\xfe\xba\xbe":
        return "macho"
    return "unknown"


def stage(outcome="not_run", exit_code=None, source="collector", elapsed=None):
    return {
        "outcome": outcome,
        "exit_code": exit_code,
        "source": source,
        "elapsed_seconds": elapsed,
    }


def inspect(kind, repo, source_sha, manifests):
    if source_sha is None:
        try:
            source_sha = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=repo,
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except (OSError, subprocess.SubprocessError):
            source_sha = "unknown"
    tools = []
    for name in TOOLS:
        found = shutil.which(name)
        tools.append(
            {
                "name": name,
                "path_class": path_class(found),
                "version": tool_version(name) if found else "unknown",
                "executable_type": executable_type(found),
            }
        )
    hashes = []
    for manifest in manifests:
        hashes.append(hashlib.sha256(Path(manifest).read_bytes()).hexdigest())
    return {
        "schema_version": 1,
        "source_sha": source_sha,
        "environment": {"kind": kind},
        "host": host_facts(),
        "tools": tools,
        "selection": {"manifest_sha256": hashes},
        "stages": {name: stage() for name in STAGES},
        "assessment": {name: "not_qualified" for name in ("l0", "l1", "l2", "l3")},
        "privacy": {"secrets_included": False, "raw_paths_included": False},
        "evidence_tier": "inventory",
        "limitations": [
            "Inspection records host facts only; it does not prove installation or operation."
        ],
        "provenance": {
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "collector": "collect-bootstrap-acceptance.py",
        },
    }


def reject_secrets(value, where="root"):
    if isinstance(value, dict):
        for key, child in value.items():
            if SECRET_KEY.search(str(key)) and not (
                where == "receipt" and key == "secrets_included"
            ):
                raise ReceiptError(f"secret-like field name refused: {key}")
            reject_secrets(child, where)
    elif isinstance(value, list):
        for child in value:
            reject_secrets(child, where)


def validate(receipt, expected_sha=None):
    keys = {
        "schema_version",
        "source_sha",
        "environment",
        "host",
        "tools",
        "selection",
        "stages",
        "assessment",
        "privacy",
        "evidence_tier",
        "limitations",
        "provenance",
    }
    if not isinstance(receipt, dict) or set(receipt) != keys:
        raise ReceiptError("receipt fields do not match schema")
    if receipt["schema_version"] != 1:
        raise ReceiptError("unsupported schema_version")
    if receipt["source_sha"] != "unknown" and not SHA.fullmatch(receipt["source_sha"]):
        raise ReceiptError("source_sha is not a valid SHA")
    if expected_sha and receipt["source_sha"] != expected_sha:
        raise ReceiptError("source_sha mismatch (stale receipt)")
    if receipt["environment"].get("kind") not in {
        "github-hosted",
        "owner-vm",
        "owner-physical",
        "unknown",
    }:
        raise ReceiptError("invalid environment.kind")
    hostkeys = {
        "os_id",
        "os_version",
        "os_build",
        "kernel",
        "architecture",
        "cpu_model",
    }
    if set(receipt["host"]) != hostkeys:
        raise ReceiptError("invalid host fields")
    if receipt["host"]["cpu_model"] == "unknown":
        raise ReceiptError("host.cpu_model is unknown")
    if not isinstance(receipt["tools"], list):
        raise ReceiptError("tools must be an array")
    for tool in receipt["tools"]:
        if set(tool) != {"name", "path_class", "version", "executable_type"}:
            raise ReceiptError("invalid tool fields")
        if tool["path_class"] not in {
            "system",
            "homebrew",
            "nvm",
            "runner-toolcache",
            "other",
            "unknown",
        }:
            raise ReceiptError("invalid tool path_class")
    if set(receipt["stages"]) != set(STAGES):
        raise ReceiptError("invalid stages")
    for item in receipt["stages"].values():
        if item.get("outcome") not in OUTCOMES:
            raise ReceiptError("invalid stage outcome")
        if item["outcome"] == "pass" and item.get("exit_code") != 0:
            raise ReceiptError("pass requires exit_code 0")
    if receipt["privacy"] != {"secrets_included": False, "raw_paths_included": False}:
        raise ReceiptError("privacy boundary violated")
    reject_secrets(receipt, "receipt")
    return True


def merge_record(receipt, path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    reject_secrets(data)
    stages = data.get("stages", data)
    for name in STAGES:
        if name in stages:
            value = stages[name]
            if isinstance(value, list):
                # Multiple attempts count as pass only if every attempt passed.
                value = (
                    value[-1]
                    if value and all(x.get("outcome") == "pass" for x in value)
                    else next(
                        (x for x in value if x.get("outcome") == "fail"),
                        value[-1] if value else stage(),
                    )
                )
            receipt["stages"][name] = {**stage(), **value}
    return receipt


def atomic_write(path, data, force=False):
    target = Path(path)
    if target.is_symlink():
        raise ReceiptError("refusing symlink output target")
    parent = target.parent.resolve()
    target = parent / target.name
    if target.exists() and not force:
        raise ReceiptError("output exists; use --force")
    encoded = json.dumps(data, indent=2, sort_keys=True) + "\n"
    fd, temp = tempfile.mkstemp(dir=parent, prefix=".receipt-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(encoded)
        os.replace(temp, target)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--inspect", action="store_true")
    modes.add_argument("--validate", metavar="PATH")
    parser.add_argument("--record", action="append", default=[])
    parser.add_argument("--out")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--source-sha")
    parser.add_argument("--repo", default=".")
    parser.add_argument(
        "--environment-kind",
        choices=("github-hosted", "owner-vm", "owner-physical", "unknown"),
        default="unknown",
    )
    parser.add_argument("--manifest", action="append", default=[])
    args = parser.parse_args()
    try:
        if args.validate:
            receipt = json.loads(Path(args.validate).read_text(encoding="utf-8"))
            validate(receipt, args.source_sha)
            print("receipt valid")
            return 0
        result = inspect(
            args.environment_kind, args.repo, args.source_sha, args.manifest
        )
        for record in args.record:
            result = merge_record(result, record)
        validate(result)
        if args.out:
            atomic_write(args.out, result, args.force)
        else:
            print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, json.JSONDecodeError, ReceiptError) as error:
        print(f"receipt error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
