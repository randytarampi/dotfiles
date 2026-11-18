#!/usr/bin/env python3
"""Report the PE architecture of executables used by the deployment tools."""

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

MACHINES = {
    0x8664: "AMD64",
    0xAA64: "ARM64",
    0xA641: "ARM64EC",
    0xA64E: "ARM64X",
    0x014C: "i386",
}


def pe_architecture_from_bytes(data):
    if len(data) < 64:
        return "unknown", "file is under 64 bytes"
    if data[:2] != b"MZ":
        return "unknown", "missing MZ signature"
    offset = struct.unpack_from("<I", data, 0x3C)[0]
    if offset > len(data) or offset > 1024 * 1024:
        return "unknown", f"e_lfanew offset {offset} is beyond allowed bounds"
    if offset + 6 > len(data):
        return "unknown", "truncated PE/COFF header"
    if data[offset : offset + 4] != b"PE\0\0":
        return "unknown", "missing PE signature"
    machine = struct.unpack_from("<H", data, offset + 4)[0]
    return MACHINES.get(machine, f"unknown (0x{machine:04X})"), None


MAX_HEADER_BYTES = 1024 * 1024 + 64


def candidate_paths(path):
    """Yield the path as given, then its Windows form when cygpath exists.

    A shell in Git Bash resolves `bash` to `/usr/bin/bash`, which a native
    Windows Python cannot open; `cygpath -w` bridges that gap without guessing.
    """
    yield Path(path)
    if os.name == "nt" or shutil.which("cygpath"):
        try:
            converted = subprocess.run(
                ["cygpath", "-w", str(path)],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            converted = ""
        if converted and converted != str(path):
            yield Path(converted)


def display_path(path):
    """Replace the owner's home prefix so output never prints a personal path."""
    if not path:
        return path
    home = os.path.expanduser("~")
    text = str(path)
    if home and home != "~" and text.startswith(home):
        return "~" + text[len(home) :]
    return text


def pe_architecture(path):
    last_error = "cannot read executable"
    for candidate in candidate_paths(path):
        try:
            with open(candidate, "rb") as handle:
                data = handle.read(MAX_HEADER_BYTES)
        except (OSError, ValueError) as error:
            last_error = f"cannot read executable: {error}"
            continue
        return pe_architecture_from_bytes(data)
    return "unknown", last_error


def executable_path(name):
    found = shutil.which(name)
    if not found:
        return None, "not found on PATH"
    return found, "command -v"


def resolve_bash(overrides):
    if "bash" in overrides:
        return {"path": overrides["bash"], "resolution": "--path override"}
    launcher, reason = executable_path("bash")
    if not launcher:
        return {"path": None, "resolution": reason}
    try:
        result = subprocess.run(
            ["readlink", "-f", launcher], capture_output=True, text=True, check=True
        )
        target = result.stdout.strip()
        if target:
            return {
                "path": target,
                "resolution": "readlink -f (running shell implementation)",
            }
    except (OSError, subprocess.CalledProcessError):
        pass
    return {
        "path": launcher,
        "resolution": "launcher only; running shell implementation could not be resolved",
    }


def resolve_tool(name, overrides):
    if name in overrides:
        return {"path": overrides[name], "resolution": "--path override"}
    path, resolution = executable_path(name)
    return {"path": path, "resolution": resolution}


def resolve_make(overrides):
    shim = resolve_tool("make", overrides)
    if not shim["path"]:
        return {
            "shim": shim,
            "target": {"path": None, "resolution": shim["resolution"]},
        }
    shim_path = Path(shim["path"])
    metadata = Path(str(shim_path) + ".shim")
    try:
        content = metadata.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"(?im)^\s*(?:path|target)\s*[:=]\s*(.+?)\s*$", content)
        if not match:
            candidates = [
                line.strip().strip('"') for line in content.splitlines() if line.strip()
            ]
            match = next(
                (candidate for candidate in candidates if Path(candidate).is_file()),
                None,
            )
        if not match:
            raise ValueError("target path not found in shim metadata")
        target = match.group(1).strip().strip('"') if hasattr(match, "group") else match
        return {
            "shim": shim,
            "target": {"path": target, "resolution": f"adjacent {metadata.name}"},
        }
    except (OSError, ValueError) as error:
        return {
            "shim": shim,
            "target": {
                "path": None,
                "resolution": f"cannot resolve ShimGen target: {error}",
            },
        }


def entry(path, resolution):
    if not path:
        return {
            "path": None,
            "architecture": "unknown",
            "reason": resolution,
            "resolution": resolution,
        }
    architecture, reason = pe_architecture(path)
    return {
        "path": display_path(path),
        "architecture": architecture,
        "reason": reason,
        "resolution": resolution,
    }


def build_report(overrides):
    bash = resolve_bash(overrides)
    make = resolve_make(overrides)
    chezmoi = resolve_tool("chezmoi", overrides)
    python = {
        "path": overrides.get("python", sys.executable),
        "resolution": "--path override" if "python" in overrides else "sys.executable",
    }
    return {
        "bash": entry(bash["path"], bash["resolution"]),
        "make": {
            "shim": entry(make["shim"]["path"], make["shim"]["resolution"]),
            "target": entry(make["target"]["path"], make["target"]["resolution"]),
        },
        "chezmoi": entry(chezmoi["path"], chezmoi["resolution"]),
        "python": entry(python["path"], python["resolution"]),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="override a resolved executable path (repeatable)",
    )
    args = parser.parse_args()
    overrides = {}
    for value in args.path:
        if "=" not in value:
            parser.error("--path must be NAME=PATH")
        name, path = value.split("=", 1)
        if name not in {"bash", "make", "chezmoi", "python"} or not path:
            parser.error("--path requires a supported name and nonempty path")
        overrides[name] = path
    try:
        report = build_report(overrides)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            for name, details in report.items():
                values = (
                    [details]
                    if name != "make"
                    else [details["shim"], details["target"]]
                )
                for value in values:
                    label = (
                        name
                        if name != "make"
                        else f"make {('shim' if value is details['shim'] else 'target')}"
                    )
                    print(
                        f"{label}: {value['architecture']} ({value['path'] or value['reason']}; {value['resolution']})"
                    )
        return 0
    except (
        Exception
    ) as error:  # Report only fails if serialization/output cannot be produced.
        print(f"could not produce architecture report: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
