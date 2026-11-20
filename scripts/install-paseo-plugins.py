#!/usr/bin/env python3
"""Owner-gated Paseo plugin reconciliation; never run from make deploy."""

import argparse
import json
import os
import platform as platform_module
import re
import shlex
import stat
import shutil
import subprocess  # nosec B404 - fixed argv, no shell.
import sys
import tempfile
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
    """Modify only a recognizable owner wrapper; preserve backups and file mode."""
    path = Path(path)
    if path.is_symlink():
        return "unsafe-wrapper"
    if not path.exists():
        return "owner-persistence-needed"
    if not path.is_file():
        return "unsafe-wrapper"
    content = path.read_text(encoding="utf-8")
    daemon_exec = re.search(r"(?m)^[ \t]*exec\s+[^\n]*paseo[^\n]*daemon\s+run\b", content)
    if daemon_exec is None:
        return "unrecognized-wrapper"
    block = (
        f"{START}\nexport PASEO_SHARED_BROWSER_CHROMIUM_EXECUTABLE="
        f"{shlex.quote(chromium)}\n{END}"
    )
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
    if content.count(START) != content.count(END) or content.count(START) > 1:
        return "unsafe-wrapper"
    if pattern.search(content):
        updated = pattern.sub(lambda _: block, content, count=1)
    else:
        updated = content[: daemon_exec.start()] + block + "\n" + content[daemon_exec.start() :]
    if updated == content:
        return "written"
    if backup:
        backup_path = Path(str(path) + ".bak")
        if backup_path.is_symlink() or (
            backup_path.exists() and backup_path.read_bytes() != path.read_bytes()
        ):
            return "backup-conflict"
        if not backup_path.exists():
            shutil.copy2(path, backup_path)
    fd, temporary = tempfile.mkstemp(prefix=".paseo-wrapper.", dir=path.parent)
    try:
        os.fchmod(fd, stat.S_IMODE(path.stat().st_mode))
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(updated)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
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
    try:
        result = run(
            [paseo, "plugin", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("plugin command could not complete; output redacted") from error
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
            subprocess.run(  # nosec B603 - resolved Paseo executable, fixed version argv
                [paseo, "--version"],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            ).stdout
        )
        # Do not authorize mutations using only a newer CLI when the running
        # daemon's compatibility version cannot be established.
        observed_daemon_version = daemon_version(paseo)
        if observed_daemon_version is None and args.apply and not args.dry_run:
            raise ValueError("daemon version unavailable; refusing plugin mutation")
        version = observed_daemon_version or cli_version
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
            subprocess.run(  # nosec B603 - absolute, resolved Paseo CLI; fixed read-only argv
                [paseo, "plugin", "ls", "--json"],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            ).stdout
        )
        if not isinstance(current, list) or any(
            not isinstance(entry, dict)
            or not isinstance(entry.get("id"), str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", entry["id"])
            for entry in current
        ):
            raise ValueError("plugin list has unsafe or malformed runtime ids")
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
        chromestatus, chromium = resolve_chromium()
        if chromium and any(char in chromium for char in ("\n", "\r", "\x00")):
            raise ValueError("unsafe Chromium executable path")
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
        if "omercnet/shared-browser" in eligible and chromestatus != "resolved":
            print(f"shared-browser Chromium: {chromestatus}")
        if args.dry_run:
            return 0
        if os.environ.get(GATE) != "1":
            raise ValueError(f"explicit --apply requires {GATE}=1")
        env = os.environ.copy()
        if chromium and "omercnet/shared-browser" in eligible:
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
        if chromium and "omercnet/shared-browser" in eligible:
            system = platform_module.system()
            home = Path.home()
            if system == "Darwin":
                mechanism = managed_wrapper(home / ".paseo/launchd-daemon.sh", chromium)
                if mechanism != "written":
                    failures.append("shared-browser persistence")
                result = subprocess.run(  # nosec B603 B607 - fixed launchctl subcommand, no shell
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
                if result.returncode:
                    failures.append("shared-browser launchctl setenv")
                print(
                    f"Chromium persistence: {mechanism}; "
                    "daemon restart required"
                )
            elif system == "Linux":
                target = home / ".config/environment.d/90-paseo-shared-browser.conf"
                if target.is_symlink():
                    failures.append("shared-browser Linux environment symlink")
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(
                        f"PASEO_SHARED_BROWSER_CHROMIUM_EXECUTABLE={chromium}\n"
                    )
                    target.chmod(0o600)
                    print(f"Chromium persistence: {target}; daemon restart required")
            elif system == "Windows":
                result = subprocess.run(  # nosec B603 B607 - fixed user-scope setx, no shell
                    ["setx", "PASEO_SHARED_BROWSER_CHROMIUM_EXECUTABLE", chromium],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if result.returncode:
                    failures.append("shared-browser setx")
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
