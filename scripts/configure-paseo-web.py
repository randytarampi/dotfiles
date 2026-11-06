#!/usr/bin/env python3
"""Opt-in Paseo Web UI with the same Caddy access policy as other web services.

Keep the native daemon bound to loopback. Caddy owns network-facing TLS and
LAN/public policy; Paseo owns its password, API and WebSocket authentication.
Never start a daemon or change relay without explicit owner-only --apply;
never overwrite user-owned concurrent changes.
"""

import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess  # nosec B404 - absolute local CLI, fixed arguments.
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from cli_helpers import add_common_args  # noqa: E402
from paseo_web_policy import (  # noqa: E402
    PaseoNotConfigured,
    RELAY_KEY,
    WEB_UI_KEY,
    desired_relay_state,
    domains_for_access,
    validate_native_config,
)

GATE = "DOTFILES_RUN_PASEO_WEB_SETUP"
CONFIG_JSON = "config.json"

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
    if not home.exists() or not (home / CONFIG_JSON).exists():
        raise PaseoNotConfigured("Paseo config not found")
    path = tailnet.checked_config(home)
    data = tailnet.decode_config(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Paseo configuration must be an object")
    return path, data


def desired_config(original, access):
    """Validate native policy and add only the Web UI, relay and Caddy hostnames.

    Relay reconciles toward the ~/.env-declared DOTFILES_PASEO_RELAY posture
    (default disabled) instead of failing on drift: Paseo's own lifecycle
    re-enables it, so the deploy corrects it with a warning.
    """
    hostnames = validate_native_config(original, access)
    wanted = domains_for_access(access)
    target = copy.deepcopy(original)
    target.setdefault("features", {}).setdefault("webUi", {})["enabled"] = True
    if wanted or "hostnames" in original.get("daemon", {}):
        target.setdefault("daemon", {})["hostnames"] = hostnames + [
            name for name in wanted if name not in hostnames
        ]
    target.setdefault("daemon", {}).setdefault("relay", {})[
        "enabled"
    ] = desired_relay_state()
    return target


def planned_mutations(original, desired):
    changes = []
    before_web = original.get("features", {}).get("webUi", {}).get("enabled")
    if before_web is not True:
        changes.append((WEB_UI_KEY, True))
    desired_relay = desired.get("daemon", {}).get("relay", {}).get("enabled")
    actual_relay = original.get("daemon", {}).get("relay", {}).get("enabled")
    if desired_relay is not None and actual_relay != desired_relay:
        changes.append((RELAY_KEY, desired_relay))
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


def _set_expected(config, key, value):
    if key == WEB_UI_KEY:
        config.setdefault("features", {}).setdefault("webUi", {})["enabled"] = value
    elif key == RELAY_KEY:
        config.setdefault("daemon", {}).setdefault("relay", {})["enabled"] = value
    else:
        config.setdefault("daemon", {})["hostnames"] = value


def _apply_changes(paseo_bin, home, original, changes):
    intermediate = copy.deepcopy(original)
    for key, value in changes:
        _, latest = read_config(home)
        if not tailnet.same_config(latest, intermediate):
            raise ValueError("Paseo config changed before the write; aborting")
        _set_expected(intermediate, key, value)
        apply_one(paseo_bin, home, key, value, intermediate)


def _stage_changes(paseo_bin, path, original, changes):
    """Preflight every CLI setter against an isolated copy of user state."""
    with tempfile.TemporaryDirectory(prefix="dotfiles-paseo-web-") as scratch:
        staged_home = Path(scratch)
        staged = staged_home / CONFIG_JSON
        staged.write_bytes(path.read_bytes())
        staged.chmod(0o600)
        _apply_changes(paseo_bin, staged_home, original, changes)


def _backup_config(path):
    backup = path.with_name("config.json.pre-web-ui")
    # O_EXCL prevents clobbering a prior backup or following a symlink.
    try:
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if not backup.is_file() or backup.is_symlink():
            raise ValueError("unsafe Paseo Web UI backup path")
    else:
        with os.fdopen(fd, "wb") as handle:
            handle.write(path.read_bytes())


def configure(paseo_bin, home, original, desired, no_backup):
    """Stage, back up and apply additive settings without unsafe rollback."""
    changes = planned_mutations(original, desired)
    if not changes:
        print("Paseo Web UI and hostname allowlist already configured")
        return
    if (RELAY_KEY, False) in changes:
        print(
            "Paseo relay drifted from the declared DOTFILES_PASEO_RELAY posture; "
            "reconciling back to disabled"
        )
    if (RELAY_KEY, True) in changes:
        print(
            "Reconciling Paseo relay on to the declared DOTFILES_PASEO_RELAY='1' "
            "hosted-relay posture; the Caddy Web UI route remains served"
        )
    path = tailnet.checked_config(home)
    _stage_changes(paseo_bin, path, original, changes)
    _, still_current = read_config(home)
    if not tailnet.same_config(still_current, original):
        raise ValueError("Paseo config changed during preflight; aborting")
    if not no_backup:
        _backup_config(path)
    _apply_changes(paseo_bin, home, original, changes)
    result = paseo_cli(paseo_bin, home, "reload")
    if result.returncode:
        print("Paseo reload failed; restart the existing daemon to serve the Web UI")
    else:
        print(
            "Paseo settings saved and reload requested; restart the daemon to serve the Web UI"
        )


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
            home = Path(
                os.environ.get("PASEO_HOME", str(Path.home() / ".paseo"))
            ).expanduser()
            if not home.exists() or not (home / CONFIG_JSON).exists():
                raise PaseoNotConfigured("Paseo config not found")
            if not args.apply or args.dry_run:
                print("Paseo CLI not installed; no changes")
                return 0
            raise ValueError("Paseo CLI required for Web UI setup")
        home = Path(
            os.environ.get("PASEO_HOME", str(Path.home() / ".paseo"))
        ).expanduser()
        _, original = read_config(home)
        access = (
            os.environ.get("CADDY_ACCESS", "localhost").strip().lower() or "localhost"
        )
        desired = desired_config(original, access)
        if not args.apply or args.dry_run:
            print(
                f"Paseo Web UI: {len(planned_mutations(original, desired))} changes proposed; no changes made"
            )
            return 0
        configure(paseo_bin, home, original, desired, args.no_backup)
        return 0
    except PaseoNotConfigured:
        print(
            "Paseo config not found; skipping Paseo Web UI lane — set the native password, then explicitly run `configure-paseo-web.py --apply` with other Paseo writers quiescent before redeploying"
        )
        return 0
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        print(f"Paseo Web UI configuration refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
