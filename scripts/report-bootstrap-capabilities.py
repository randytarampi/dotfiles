#!/usr/bin/env python3
"""Read-only bootstrap capability inventory. Presence is never operational proof."""

import argparse
import json
import os
import platform
from pathlib import Path
import shutil

CORE_TOOLS = ("bash", "make", "chezmoi")
PACKAGE_TOOLS = ("apt-get", "brew", "dnf", "pacman", "winget")
AGENT_TOOLS = (
    "claude",
    "codex",
    "cursor-agent",
    "gemini",
    "hermes",
    "junie",
    "opencode",
    "pi",
)


def normalize_arch(raw):
    name = (raw or "").strip().lower()
    if name in ("x86_64", "amd64"):
        return "amd64"
    if name in ("aarch64", "arm64"):
        return "arm64"
    if name in ("arm", "armv6l", "armv7l", "armv8l"):
        return "arm32"
    if name in ("ppc64", "ppc64le"):
        return "power"
    return "unknown"


def parse_os_release(contents):
    data = {}
    for line in contents.splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        if key in ("ID", "VERSION_ID"):
            data[key] = value.strip().strip('"').strip("'")
    return data


def parse_memtotal_bytes(contents):
    for line in contents.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] == "MemTotal:" and parts[2] == "kB":
            try:
                size = int(parts[1])
            except ValueError:
                return None
            return size * 1024 if size > 0 else None
    return None


def assess(family, architecture, tools, memory_bytes, display):
    normalized = normalize_arch(architecture)
    known_os = family in ("Linux", "Darwin", "Windows")
    mainstream = known_os and normalized in ("amd64", "arm64")
    experimental = known_os and normalized in ("arm32", "power")
    missing = [name for name in CORE_TOOLS if name not in tools]

    notes = [
        "Command presence is not proof of successful deployment or authentication",
        "No credentials, services, package installation or inference were exercised",
        "Physical Raspberry Pi/Pine hardware identity is not inferred from CPU type",
    ]
    if experimental:
        notes.append("CPU is experimental; physical host evidence is required")
    if not known_os or normalized == "unknown":
        notes.append("OS or CPU is unqualified; do not claim compatibility")
    if display == "not_advertised":
        notes.append(
            "No Linux display environment advertised; not proof of headlessness"
        )
    if memory_bytes is not None and memory_bytes < 4 * 1024**3:
        notes.append("Memory below 4 GiB; heavyweight local model eligibility unknown")

    eligibility = "not_qualified"
    if not missing:
        if mainstream:
            eligibility = "candidate_unverified"
        elif experimental:
            eligibility = "experimental_unverified"
    return {
        "architecture_class": normalized,
        "core_l0": eligibility,
        "missing_core_tools": missing,
        "packages_l1": "not_qualified",
        "agents_l2": "not_qualified",
        "services_l3": "not_qualified",
        "operational_verified": False,
        "evidence": "read_only_inventory",
        "notes": notes,
    }


def read_optional(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


def inventory():
    family = platform.system()
    arch = platform.machine()
    os_info = (
        parse_os_release(read_optional("/etc/os-release")) if family == "Linux" else {}
    )
    memory = (
        parse_memtotal_bytes(read_optional("/proc/meminfo"))
        if family == "Linux"
        else None
    )
    display = "unknown"
    if family == "Linux":
        display = (
            "advertised"
            if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
            else "not_advertised"
        )
    found = {
        name for name in CORE_TOOLS + PACKAGE_TOOLS + AGENT_TOOLS if shutil.which(name)
    }
    service_manager_tool = {"Linux": "systemctl", "Darwin": "launchctl"}.get(family)
    service_manager_present = bool(
        service_manager_tool and shutil.which(service_manager_tool)
    )
    return {
        "schema_version": 1,
        "host": {
            "os_family": family,
            "os_id": os_info.get("ID", family.lower()),
            "os_version": os_info.get("VERSION_ID", platform.release()),
            "architecture": arch,
            "memory_gib": round(memory / 1024**3, 2) if memory else None,
            "display_environment": display,
            "package_tools_present": sorted(found.intersection(PACKAGE_TOOLS)),
            "service_manager_binary_present": service_manager_present,
            "agent_clis_present": sorted(found.intersection(AGENT_TOOLS)),
            "core_tools_present": sorted(found.intersection(CORE_TOOLS)),
            "hardware_identity": "not_assessed",
        },
        "assessment": assess(family, arch, found, memory, display),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    args = parser.parse_args()
    result = inventory()
    if args.json:
        print(json.dumps(result, sort_keys=True, indent=2))
    else:
        host, status = result["host"], result["assessment"]
        print(
            f"{host['os_id']} {host['os_version']} {host['architecture']}: "
            f"L0={status['core_l0']}; "
            "L1/L2/L3=not_qualified (read-only inventory)"
        )
        print("Core tools present:", ", ".join(host["core_tools_present"]) or "none")
        print("Missing core tools:", ", ".join(status["missing_core_tools"]) or "none")
        for note in status["notes"]:
            print("-", note)


if __name__ == "__main__":
    main()
