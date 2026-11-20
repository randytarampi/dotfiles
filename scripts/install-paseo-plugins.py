#!/usr/bin/env python3
"""Owner-gated Paseo plugin reconciliation; never run from make deploy."""

import argparse
import json
import os
import platform as platform_module
import re
import shutil
import subprocess  # nosec B404 - fixed argv, no shell.
import sys
from pathlib import Path

PLUGINS = (
    ("stevecastaneda/progress", "node-22.18"),
    ("uiyzzi/codexbar-usage", "codexbar"),
    ("roundreddot/better-paseo", "paseo-0.12"),
    ("gpambrozio/skills", None),
    ("dutchakdev/machine-status", "paseo-0.8-through-0.13"),
    ("dutchakdev/mermaid", None),
    ("nkwib/doc-viewer", None),
    ("asherlc/devices", "paseo-0.16.2"),
    ("omercnet/context-mode", None),
    ("asherlc/cheapskate", "paseo-0.16.1"),
    ("omercnet/shared-browser", "node-24-paseo-0.13"),
)
GATE = "DOTFILES_RUN_PASEO_PLUGINS_SETUP"
START = "# >>> dotfiles: shared-browser chromium >>>"
END = "# <<< dotfiles: shared-browser chromium <<<"


def version_tuple(value):
    match = re.fullmatch(r"(?:paseo\s+)?v?(\d+)\.(\d+)\.(\d+)", value.strip())
    if not match:
        raise ValueError("Paseo version unknown; refusing plugin installation")
    return tuple(int(part) for part in match.groups())


def qualified_node_version(which, run):
    binary = which("node")
    if not binary or not os.path.isabs(binary):
        return None
    try:
        result = run(
            [binary, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return version_tuple(result.stdout) if result.returncode == 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def resolve_chromium(
    system=None, which=shutil.which, exists=os.path.isfile, environ=None
):
    system = system or platform_module.system()
    environ = os.environ if environ is None else environ
    if system == "Darwin":
        candidates = ["/Applications/Chromium.app/Contents/MacOS/Chromium"]
    elif system == "Windows":
        candidates = [
            environ.get(k, "").rstrip("\\/") + "\\Chromium\\Application\\chrome.exe"
            for k in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)")
            if environ.get(k)
        ]
        candidates.append(which("chrome"))
    elif system == "Linux":
        candidates = [
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            which("chromium"),
        ]
    else:
        return "not-needed", None
    for candidate in candidates:
        if (
            candidate
            and (
                os.path.isabs(candidate)
                or (system == "Windows" and re.match(r"^[A-Za-z]:\\", candidate))
            )
            and exists(candidate)
            and not candidate.startswith("/snap/")
        ):
            return "resolved", candidate
    return "unresolved", None


def managed_wrapper(path, chromium, *, backup=True):
    path = Path(path)
    if not path.exists():
        return "owner-persistence-needed"
    content = path.read_text()
    if not re.search(r"^\s*exec\s+.*paseo.*daemon run", content, re.M):
        return "unrecognized-wrapper"
    block = (
        f'{START}\nexport PASEO_SHARED_BROWSER_CHROMIUM_EXECUTABLE="{chromium}"\n{END}'
    )
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
    updated = (
        pattern.sub(block, content)
        if pattern.search(content)
        else content.replace("exec", block + "\nexec", 1)
    )
    if updated != content:
        if backup:
            shutil.copy2(path, str(path) + ".bak")
        path.write_text(updated)
    return "written"


def plan(
    which=None,
    version=None,
    *,
    platform=None,
    node_version=None,
    daemon_codexbar_verified=False,
    current=None,
):
    """Return registry-id add/skip checks while retaining legacy callers."""
    if version is None and isinstance(which, list):
        return reconcile(which)
    which = which or shutil.which
    platform = sys.platform if platform is None else platform
    selected, omitted = [], []
    for name, requirement in PLUGINS:
        reason = None
        if name == "stevecastaneda/progress" and (
            node_version is None or node_version < (22, 18, 0)
        ):
            reason = "Node >=22.18 required"
        elif name == "uiyzzi/codexbar-usage" and (
            not daemon_codexbar_verified or not which("codexbar")
        ):
            reason = "codexbar must be verified on daemon PATH"
        elif name == "roundreddot/better-paseo" and version < (0, 12, 0):
            reason = "Paseo >=0.12 required"
        elif requirement == "paseo-0.8-through-0.13" and not (
            (0, 8, 0) <= version < (0, 14, 0)
        ):
            reason = "published support ends at Paseo 0.13"
        elif requirement == "paseo-0.8-through-0.13" and platform not in (
            "darwin",
            "Darwin",
        ):
            reason = "macOS required"
        elif name == "omercnet/shared-browser" and (
            node_version is None or node_version < (24, 0, 0) or version < (0, 13, 0)
        ):
            reason = "Node >=24 and Paseo >=0.13 required"
        elif requirement == "paseo-0.16.2" and version < (0, 16, 2):
            # Registry-declared floor, reported by the daemon on install.
            reason = "Paseo >=0.16.2 required"
        elif requirement == "paseo-0.16.1" and version < (0, 16, 1):
            reason = "Paseo >=0.16.1 required"
        if reason:
            omitted.append((name, reason))
        else:
            selected.append(name)
    if current is None:
        return selected, omitted
    adds, enables, unlisted = reconcile(current)
    return adds, enables, unlisted


def reconcile(current):
    by_registry = {
        p.get("installation", {}).get("identity", {}).get("registry", {}).get("id"): p
        for p in current
    }
    wanted = {name for name, _ in PLUGINS}
    adds, enables, unlisted = [], [], []
    for name, _ in PLUGINS:
        entry = by_registry.get(name)
        if entry is None:
            adds.append(name)
        elif not entry.get("enabled", True):
            enables.append((name, entry["id"]))
    for entry in current:
        registry = (
            entry.get("installation", {})
            .get("identity", {})
            .get("registry", {})
            .get("id")
        )
        if registry not in wanted:
            unlisted.append((registry or "unknown", entry.get("id", "unknown")))
    return adds, enables, unlisted


def daemon_version(paseo, run=None):
    """Return the running daemon's version, which is what plugin installs gate on.

    The CLI binary and the daemon can diverge after an upgrade until the daemon
    restarts; using the CLI version would plan installs the daemon then refuses.
    Returns None when the daemon version cannot be read.
    """
    if run is None:
        run = subprocess.run
    try:
        result = run(
            [paseo, "status", "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            payload = json.loads(result.stdout or "{}")
            return version_tuple(str(payload.get("daemonVersion", "")))
    except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError):
        pass
    return None


def run_plugin(paseo, args, *, env=None, run=None):
    if run is None:
        # Resolve at call time so injected/monkeypatched runners are honoured.
        run = subprocess.run
    result = run(
        [paseo, "plugin", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )
    if result.returncode:
        raise ValueError(
            f"plugin command failed (exit {result.returncode}; output redacted)"
        )
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--prune", action="store_true", help="Remove unlisted installed plugins"
    )
    parser.add_argument("--codexbar-daemon-verified", action="store_true")
    args = parser.parse_args(argv)
    if not args.apply:
        args.dry_run = True
    paseo = shutil.which("paseo")
    if not paseo:
        print("Paseo not installed; no changes")
        return 0 if args.dry_run else 1
    try:
        cli_version = version_tuple(
            subprocess.run(
                [paseo, "--version"],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            ).stdout
        )
        # The daemon enforces plugin compatibility floors, so plan against it.
        version = daemon_version(paseo) or cli_version
        if version != cli_version:
            print(
                "Note: daemon "
                + ".".join(map(str, version))
                + " differs from CLI "
                + ".".join(map(str, cli_version))
                + "; plugin floors use the daemon version"
            )
        node = qualified_node_version(shutil.which, subprocess.run)
        current = json.loads(
            subprocess.run(
                [paseo, "plugin", "ls", "--json"],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            ).stdout
        )
        eligible, omitted = plan(
            shutil.which,
            version,
            node_version=node,
            daemon_codexbar_verified=args.codexbar_daemon_verified,
        )
        installs, enables, unlisted = reconcile(current)
        installs = [name for name in installs if name in eligible]
        # A listed-but-disabled plugin is only re-enabled when its prerequisites
        # are met; eligibility must gate enables exactly as it gates installs.
        enables = [(name, runtime) for name, runtime in enables if name in eligible]
        installs = [
            n
            for n in installs
            if n != "uiyzzi/codexbar-usage"
            or (args.codexbar_daemon_verified and shutil.which("codexbar"))
        ]
        if node is None or node < (24, 0, 0):
            installs = [n for n in installs if n != "omercnet/shared-browser"]
        if version < (0, 13, 0):
            installs = [n for n in installs if n != "omercnet/shared-browser"]
        chromestatus, chromium = resolve_chromium()
        for name, reason in omitted:
            print(f"Skip {name}: {reason}")
        for name in installs:
            print(f"{'Would install' if args.dry_run else 'Install'} {name}")
        for name, runtime in enables:
            print(f"{'Would enable' if args.dry_run else 'Enable'} {name} ({runtime})")
        for registry, runtime in unlisted:
            print(
                f"Unlisted installed plugin: {registry} ({runtime})"
                + ("; would remove" if args.prune else "; retained")
            )
        if chromestatus != "resolved":
            print(f"shared-browser Chromium: {chromestatus}")
        if args.dry_run:
            return 0
        if os.environ.get(GATE) != "1":
            raise ValueError(f"explicit --apply requires {GATE}=1")
        env = os.environ.copy()
        if chromium:
            env["PASEO_SHARED_BROWSER_CHROMIUM_EXECUTABLE"] = chromium
        failures = []
        for name in installs:
            try:
                run_plugin(paseo, ["add", name], env=env)
            except ValueError as exc:
                failures.append(name)
                print(f"Failed {name}: {exc}", file=sys.stderr)
        for name, runtime in enables:
            try:
                run_plugin(paseo, ["enable", runtime])
            except ValueError as exc:
                failures.append(name)
                print(f"Failed to enable {name}: {exc}", file=sys.stderr)
        if args.prune:
            for registry, runtime in unlisted:
                try:
                    run_plugin(paseo, ["remove", runtime])
                except ValueError as exc:
                    failures.append(registry)
                    print(f"Failed to remove {registry}: {exc}", file=sys.stderr)
        if chromium:
            system = platform_module.system()
            home = Path.home()
            if system == "Darwin":
                mechanism = managed_wrapper(home / ".paseo/launchd-daemon.sh", chromium)
                subprocess.run(
                    [
                        "launchctl",
                        "setenv",
                        "PASEO_SHARED_BROWSER_CHROMIUM_EXECUTABLE",
                        chromium,
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                print(
                    f"Chromium persistence: {mechanism}; launchctl setenv requested; daemon restart required"
                )
            elif system == "Linux":
                target = home / ".config/environment.d/90-paseo-shared-browser.conf"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    f"PASEO_SHARED_BROWSER_CHROMIUM_EXECUTABLE={chromium}\n"
                )
                print(f"Chromium persistence: {target}; daemon restart required")
            elif system == "Windows":
                subprocess.run(
                    ["setx", "PASEO_SHARED_BROWSER_CHROMIUM_EXECUTABLE", chromium],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                print("Chromium persistence: setx user scope; daemon restart required")
        if failures:
            print(
                f"{len(failures)} plugin operation(s) failed: {', '.join(failures)}",
                file=sys.stderr,
            )
            return 1
        return 0
    except (
        OSError,
        ValueError,
        subprocess.SubprocessError,
        json.JSONDecodeError,
    ) as exc:
        print(f"Paseo plugin setup refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
