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
# Plugins that must not be installed unless their own ~/.env feature gate is on.
PLUGIN_GATES = {"omercnet/context-mode": "DOTFILES_RUN_CONTEXT_MODE_SETUP"}
CONTEXT_MODE_PLUGIN = "omercnet/context-mode"
CONTEXT_MODE_SETTINGS_ID = "context-mode"
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


CONTEXT_MODE_DEFAULTS = {
    "refreshIntervalMs": 15_000,
    "autoInject": True,
    "preferNativeIntegrations": True,
}


def resolve_repo_context_mode(
    which=shutil.which, exists=os.path.isfile, realpath=os.path.realpath, home=None
):
    """Return only a present npm-managed cli.mjs, never a bundled copy or shim."""
    candidate = which("context-mode")
    if not candidate or not os.path.isabs(candidate):
        return "unresolved", None
    home = Path.home() if home is None else Path(home)
    bundled_literal = (home / ".paseo/plugins").absolute()
    bundled_root = Path(realpath(str(bundled_literal))).resolve()
    original_path = Path(os.path.abspath(candidate))
    resolved = Path(realpath(candidate)).resolve()
    if (
        bundled_literal == original_path
        or bundled_literal in original_path.parents
        or resolved == bundled_root
        or bundled_root in resolved.parents
    ):
        return "bundled", None
    # npm's global executable may be a symlink or a Windows .cmd shim. In
    # either case the plugin needs the real Node entrypoint, not a shell shim.
    paths = (
        resolved,
        Path(
            realpath(
                str(Path(candidate).parent / "../lib/node_modules/context-mode/cli.mjs")
            )
        ).resolve(),
        Path(
            realpath(str(Path(candidate).parent / "node_modules/context-mode/cli.mjs"))
        ).resolve(),
    )
    for path in paths:
        if path == bundled_root or bundled_root in path.parents:
            return "bundled", None
        if (
            path.name == "cli.mjs"
            and path.parent.name == "context-mode"
            and path.parent.parent.name == "node_modules"
            and exists(str(path))
        ):
            return "resolved", str(path)
    return "unresolved", None


def configure_context_mode_binary(settings_dir, binary, *, backup=True, dry_run=False):
    """Update only binaryPath; reject unknown user state and unsafe file paths."""
    if (
        not isinstance(binary, str)
        or not os.path.isabs(binary)
        or any(char in binary for char in ("\n", "\r", "\x00"))
    ):
        raise ValueError("unsafe context-mode binaryPath")
    directory = Path(settings_dir)
    target = directory / f"{CONTEXT_MODE_SETTINGS_ID}.json"
    for path in (directory.parent.parent, directory.parent, directory, target):
        if path.is_symlink():
            raise ValueError("context-mode settings path contains a symlink")
    if directory.exists() and not directory.is_dir():
        raise ValueError("context-mode settings directory is not a directory")
    if target.exists() and not target.is_file():
        raise ValueError("context-mode settings path is not a regular file")

    existing = {"version": 2, "values": dict(CONTEXT_MODE_DEFAULTS)}
    previous = None
    previous_stat = None
    if target.exists():
        previous_stat = target.stat()
        # POSIX ownership checks do not establish Windows ACL security.
        if os.name != "nt" and (
            previous_stat.st_uid != os.getuid()
            or stat.S_IMODE(previous_stat.st_mode) != 0o600
        ):
            raise ValueError("context-mode settings are not owner-only")
        try:
            previous = target.read_bytes()
            stored = json.loads(previous.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(
                "context-mode settings unreadable; refusing overwrite"
            ) from error
        if (
            not isinstance(stored, dict)
            or stored.get("version") != 2
            or not isinstance(stored.get("values"), dict)
        ):
            raise ValueError("context-mode settings schema unknown; refusing overwrite")
        existing.update(stored)
        existing["values"] = {**CONTEXT_MODE_DEFAULTS, **stored["values"]}

    existing["values"]["binaryPath"] = binary
    serialized = json.dumps(existing, indent=2)
    if previous is not None and previous.decode("utf-8").strip() == serialized:
        return "unchanged"
    backup_path = Path(str(target) + ".bak")
    if backup and (
        backup_path.is_symlink() or (backup_path.exists() and not backup_path.is_file())
    ):
        raise ValueError("context-mode settings backup is unsafe")
    if backup and previous is not None and backup_path.exists():
        if backup_path.read_bytes() != previous:
            raise ValueError(
                "context-mode settings backup conflict; refusing overwrite"
            )
    if dry_run:
        return "would-write"

    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Best-effort detection of competing native settings writers; users must
    # still quiesce Paseo Desktop before applying user-owned changes.
    if previous is not None:
        current_stat = target.stat()
        if (current_stat.st_dev, current_stat.st_ino, current_stat.st_mtime_ns) != (
            previous_stat.st_dev,
            previous_stat.st_ino,
            previous_stat.st_mtime_ns,
        ) or target.read_bytes() != previous:
            raise ValueError("context-mode settings changed concurrently")
    elif target.exists() or target.is_symlink():
        raise ValueError("context-mode settings appeared concurrently")
    if backup and previous is not None and not backup_path.exists():
        try:
            with backup_path.open("xb") as file:
                if hasattr(os, "fchmod"):
                    os.fchmod(file.fileno(), 0o600)
                file.write(previous)
            if not hasattr(os, "fchmod"):
                backup_path.chmod(0o600)
        except FileExistsError as error:
            raise ValueError(
                "context-mode settings backup raced with another writer"
            ) from error
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=directory)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        else:
            os.chmod(temporary, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialized)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return "written"


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
    daemon_exec = re.search(
        r"(?m)^[ \t]*exec\s+[^\n]*paseo[^\n]*daemon\s+run\b", content
    )
    if daemon_exec is None:
        return "unrecognized-wrapper"
    block = (
        f"{START}\nexport PASEO_SHARED_BROWSER_CHROMIUM_EXECUTABLE="
        f"{shlex.quote(chromium)}\n{END}"
    )
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
    if content.count(START) != content.count(END) or content.count(START) > 1:
        return "unsafe-wrapper"
    match = pattern.search(content)
    if match is not None and match.start() > daemon_exec.start():
        return "unsafe-wrapper"
    legacy_block = (
        f'{START}\nexport PASEO_SHARED_BROWSER_CHROMIUM_EXECUTABLE="{chromium}"\n{END}'
    )
    # Keep a safe block that was already installed by the owner; don't
    # rewrite it just to alter quote style or collide with its backup.
    if (
        match is not None
        and match.group() == legacy_block
        and re.fullmatch(r"[/A-Za-z0-9_. +:-]+", chromium)
    ):
        return "written"
    if match is not None:
        updated = pattern.sub(lambda _: block, content, count=1)
    else:
        updated = (
            content[: daemon_exec.start()]
            + block
            + "\n"
            + content[daemon_exec.start() :]
        )
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
    environ=None,
):
    """Return registry-id add/skip checks while retaining legacy callers."""
    if version is None and isinstance(which, list):
        return reconcile(which)
    which = which or shutil.which
    platform = sys.platform if platform is None else platform
    env = os.environ if environ is None else environ
    selected, omitted = [], []
    for name, requirement in PLUGINS:
        reason = None
        gate = PLUGIN_GATES.get(name)
        if gate is not None and env.get(gate, "0") != "1":
            reason = f"{gate} != 1"
        elif name == CONTEXT_MODE_PLUGIN and (
            node_version is None or node_version < (24, 15, 0)
        ):
            reason = "Context Mode requires Node >=24.15"
        elif name == "stevecastaneda/progress" and (
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
        raise ValueError(
            "plugin command could not complete; output redacted"
        ) from error
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
        context_installed = any(
            (entry.get("installation") or {})
            .get("identity", {})
            .get("registry", {})
            .get("id")
            == CONTEXT_MODE_PLUGIN
            for entry in current
        )
        context_needed = CONTEXT_MODE_PLUGIN in eligible and (
            context_installed or CONTEXT_MODE_PLUGIN in installs
        )
        context_binary = None
        context_settings_dir = Path.home() / ".paseo/plugin-settings/context-mode"
        if context_needed:
            status, context_binary = resolve_repo_context_mode()
            if status != "resolved" or context_binary is None:
                print(f"context-mode binary: {status}")
                if args.apply and not args.dry_run:
                    raise ValueError(
                        "managed context-mode CLI unavailable; refusing bundled fallback"
                    )
            else:
                outcome = configure_context_mode_binary(
                    context_settings_dir, context_binary, dry_run=True
                )
                print(
                    f"{'Would set' if args.dry_run else 'Setting'} "
                    f"context-mode binaryPath={context_binary} ({outcome})"
                )
        if args.dry_run:
            return 0
        if os.environ.get(GATE) != "1":
            raise ValueError(f"explicit --apply requires {GATE}=1")
        env = os.environ.copy()
        if chromium and "omercnet/shared-browser" in eligible:
            env["PASEO_SHARED_BROWSER_CHROMIUM_EXECUTABLE"] = chromium
        failures = []
        context_settings_outcome = None
        if context_binary:
            # Install/enable must never start the bundled runtime before the
            # repo-managed binary setting has been safely written.
            context_settings_outcome = configure_context_mode_binary(
                context_settings_dir, context_binary
            )
            print(f"context-mode settings: {context_settings_outcome}")
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
        if (
            context_binary
            and context_settings_outcome == "written"
            and (context_installed or CONTEXT_MODE_PLUGIN in installs)
            and CONTEXT_MODE_PLUGIN not in failures
        ):
            try:
                run_plugin(paseo, ["reload", CONTEXT_MODE_SETTINGS_ID])
            except ValueError as exc:
                failures.append("context-mode settings reload")
                print(f"Failed to reload context-mode: {exc}", file=sys.stderr)
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
                print(f"Chromium persistence: {mechanism}; " "daemon restart required")
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
                result = (
                    subprocess.run(  # nosec B603 B607 - fixed user-scope setx, no shell
                        ["setx", "PASEO_SHARED_BROWSER_CHROMIUM_EXECUTABLE", chromium],
                        check=False,
                        capture_output=True,
                        text=True,
                    )
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
