#!/usr/bin/env python3
"""Opt-in Paseo Web UI with the same Caddy access policy as other web services.

Keep the native daemon bound to loopback. Caddy owns network-facing TLS and
LAN/public policy; Paseo owns its password, API and WebSocket authentication.
Never start a daemon, enable relay or overwrite user-owned concurrent changes.
"""

import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess  # nosec B404 - absolute local CLI, fixed arguments.
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from cli_helpers import add_common_args  # noqa: E402
from caddy_domains import load_domains  # noqa: E402

GATE = "DOTFILES_RUN_PASEO_WEB_SETUP"
ACCESS_MODES = {"localhost", "lan", "public"}
DEFAULT_LISTEN = "127.0.0.1:6767"
BCRYPT = re.compile(r"\$2[aby]\$\d\d\$[./A-Za-z0-9]{53}\Z")

_loader = importlib.util.spec_from_file_location(
    "configure_paseo_tailnet", Path(__file__).parent / "configure-paseo-tailnet.py"
)
tailnet = importlib.util.module_from_spec(_loader)
_loader.loader.exec_module(tailnet)


def paseo_cli(paseo_bin, home, *args):
    """Use an explicit --home for every CLI invocation, even reload."""
    return subprocess.run(  # nosec B603 - fixed binary and validated arguments.
        [paseo_bin, "daemon", *args, "--home", str(home)],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )


def read_config(home):
    path = tailnet.checked_config(home)
    data = tailnet.decode_config(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Paseo configuration must be an object")
    return path, data


def domains_for_access(access):
    if os.environ.get("DOTFILES_RUN_CADDY_SETUP", "0") != "1":
        return []
    from pathlib import Path as _Path

    zones = _Path(
        os.path.expanduser(
            os.environ.get("CADDY_ZONES_CONFIG", "~/.config/caddy/ddns-zones.json")
        )
    )
    domains = load_domains(zones)
    if access != "localhost" and not domains:
        raise ValueError("Caddy LAN/public access requires configured domains")
    if access == "localhost":
        return ["paseo.localhost"] + [
            f"paseo.{d}" for d in domains if d.startswith("local.")
        ]
    return [f"paseo.{d}" for d in domains]


def desired_config(original, access):
    """Validate policy and compute only intentional native Paseo edits."""
    if access not in ACCESS_MODES:
        raise ValueError("invalid CADDY_ACCESS; choose localhost, lan or public")
    daemon = original.get("daemon", {})
    if not isinstance(daemon, dict):
        raise ValueError("Paseo daemon config must be an object")
    if daemon.get("listen", DEFAULT_LISTEN) != DEFAULT_LISTEN or os.environ.get(
        "PASEO_LISTEN"
    ):
        raise ValueError(
            "Web UI Caddy mode requires the loopback Paseo listener without PASEO_LISTEN override"
        )
    relay = daemon.get("relay")
    if not isinstance(relay, dict) or relay.get("enabled") is not False:
        raise ValueError("Paseo relay must be explicitly disabled")
    if os.environ.get("PASEO_RELAY_ENABLED", "").lower() not in ("", "false", "0"):
        raise ValueError("PASEO_RELAY_ENABLED could override relay isolation")
    password = (
        daemon.get("auth", {}).get("password")
        if isinstance(daemon.get("auth"), dict)
        else None
    )
    if access != "localhost" and os.environ.get("DOTFILES_RUN_CADDY_SETUP") == "1":
        if not isinstance(password, str) or not BCRYPT.fullmatch(password):
            raise ValueError("Caddy LAN/public access requires a Paseo daemon password")
    if os.environ.get("PASEO_WEB_UI_ENABLED", "").lower() not in ("", "true", "1"):
        raise ValueError("PASEO_WEB_UI_ENABLED contradicts the managed web UI setting")
    features = original.get("features", {})
    if not isinstance(features, dict) or not isinstance(
        features.get("webUi", {}), dict
    ):
        raise ValueError("Paseo features.webUi must be an object")
    hostnames = daemon.get("hostnames", [])
    if not isinstance(hostnames, list) or any(
        not isinstance(s, str) for s in hostnames
    ):
        raise ValueError("Paseo daemon.hostnames must be an array of strings")
    wanted = domains_for_access(access)
    target = copy.deepcopy(original)
    target.setdefault("features", {}).setdefault("webUi", {})["enabled"] = True
    target.setdefault("daemon", {})["hostnames"] = (
        hostnames + [s for s in wanted if s not in hostnames]
        if wanted or "hostnames" in daemon
        else hostnames
    )
    if not wanted and "hostnames" not in daemon:
        target["daemon"].pop("hostnames", None)
    return target


def planned_mutations(original, desired):
    changes = []
    before_web = original.get("features", {}).get("webUi", {}).get("enabled")
    if before_web is not True:
        changes.append(("features.webUi.enabled", True))
    old_hosts = original.get("daemon", {}).get("hostnames", [])
    new_hosts = desired.get("daemon", {}).get("hostnames", [])
    if old_hosts != new_hosts:
        changes.append(("daemon.hostnames", new_hosts))
    return changes


def apply_one(paseo_bin, home, key, value, expected):
    """Check the persisted result even if native CLI reports reload failure.

    Paseo can save a setting and then return nonzero on automatic reload.
    Accept only an exact intended write; never restore an older snapshot.
    """
    value_text = json.dumps(value, separators=(",", ":"))
    result = paseo_cli(paseo_bin, home, "config", "set", key, value_text)
    check_expected(home, expected)
    if result.returncode:
        print(
            f"Paseo saved {key} but its CLI exited {result.returncode}; daemon restart required"
        )


def check_expected(home, desired):
    _, actual = read_config(home)
    if not tailnet.same_config(actual, desired):
        raise ValueError(
            "Paseo config changed unrelated data; left live state untouched (no unsafe rollback)"
        )


def configure(paseo_bin, home, original, desired, path, no_backup):
    changes = planned_mutations(original, desired)
    if not changes:
        print("Paseo Web UI and hostname allowlist already configured")
        return 0
    # A private staged home detects lossy schema rewrites before any live mutation.
    with tempfile.TemporaryDirectory(prefix="dotfiles-paseo-web-") as scratch:
        staged_home = Path(scratch)
        staged = staged_home / "config.json"
        staged.write_bytes(path.read_bytes())
        staged.chmod(0o600)
        intermediate = copy.deepcopy(original)
        for key, value in changes:
            if key == "features.webUi.enabled":
                intermediate.setdefault("features", {}).setdefault("webUi", {})[
                    "enabled"
                ] = value
            else:
                intermediate.setdefault("daemon", {})["hostnames"] = value
            apply_one(paseo_bin, staged_home, key, value, intermediate)
    _, still_current = read_config(home)
    if not tailnet.same_config(still_current, original):
        raise ValueError("Paseo config changed during preflight; aborting")
    if not no_backup:
        backup = path.with_name("config.json.pre-web-ui")
        # O_EXCL prevents both clobbering a previous backup and symlink following.
        try:
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if not backup.is_file() or backup.is_symlink():
                raise ValueError("unsafe Paseo Web UI backup path")
        else:
            with os.fdopen(fd, "wb") as handle:
                handle.write(path.read_bytes())
    intermediate = copy.deepcopy(original)
    for key, value in changes:
        _, latest = read_config(home)
        if not tailnet.same_config(latest, intermediate):
            raise ValueError("Paseo config changed before the write; aborting")
        if key == "features.webUi.enabled":
            intermediate.setdefault("features", {}).setdefault("webUi", {})[
                "enabled"
            ] = value
        else:
            intermediate.setdefault("daemon", {})["hostnames"] = value
        apply_one(paseo_bin, home, key, value, intermediate)
    result = paseo_cli(paseo_bin, home, "reload")
    if result.returncode:
        print("Paseo reload failed; restart the existing daemon to serve the Web UI")
    else:
        print(
            "Paseo settings saved and reload requested; restart the daemon to serve the Web UI"
        )
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument(
        "--apply", action="store_true", help="Apply validated Paseo settings"
    )
    add_common_args(parser, no_backup=True)
    args = parser.parse_args(argv)
    if os.environ.get(GATE, "0") != "1":
        print(f"{GATE} disabled — no changes")
        return 0
    try:
        paseo_bin = shutil.which("paseo")
        if not paseo_bin:
            if not args.apply or args.dry_run:
                print("Paseo CLI not installed; no changes")
                return 0
            raise ValueError("Paseo CLI required for Web UI setup")
        home = Path(
            os.environ.get("PASEO_HOME", str(Path.home() / ".paseo"))
        ).expanduser()
        path, original = read_config(home)
        access = (
            os.environ.get("CADDY_ACCESS", "localhost").strip().lower() or "localhost"
        )
        desired = desired_config(original, access)
        if not args.apply or args.dry_run:
            print(
                f"Paseo Web UI: {len(planned_mutations(original, desired))} changes proposed; no changes made"
            )
            return 0
        return configure(paseo_bin, home, original, desired, path, args.no_backup)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        print(f"Paseo Web UI configuration refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
