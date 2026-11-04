#!/usr/bin/env python3
"""Safely configure an existing Paseo daemon for private Tailscale access.

Uses Paseo's validated config CLI. Never creates a daemon or enables a relay.
"""

import argparse
import copy
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess  # nosec B404 - fixed local Paseo and Tailscale CLI operations.
import sys

GATE = "DOTFILES_RUN_PASEO_SETUP"
DEFAULT_LISTEN = "127.0.0.1:6767"
_BCRYPT = re.compile(r"\$2[aby]\$\d\d\$[./A-Za-z0-9]{53}\Z")


def validate_config(data, desired):
    if not isinstance(data, dict):
        raise ValueError("Paseo config must be a JSON object")
    daemon = data.get("daemon", {})
    if not isinstance(daemon, dict):
        raise ValueError("Paseo daemon config must be an object")
    auth = daemon.get("auth", {})
    if (
        not isinstance(auth, dict)
        or not isinstance(auth.get("password"), str)
        or not _BCRYPT.fullmatch(auth["password"])
    ):
        raise ValueError("set a daemon password with 'paseo daemon set-password'")
    relay = daemon.get("relay")
    # Legacy Paseo homes may enable relay implicitly when this field is absent.
    if not isinstance(relay, dict) or relay.get("enabled") is not False:
        raise ValueError("Paseo relay must be explicitly disabled in config.json")
    current = daemon.get("listen", DEFAULT_LISTEN)
    if current not in (DEFAULT_LISTEN, desired):
        raise ValueError("existing Paseo listener is user-owned; refusing replacement")
    return "unchanged" if current == desired else "change"


def tailnet_ip(value):
    ip = ipaddress.IPv4Address(value.strip())
    if ip not in ipaddress.IPv4Network("100.64.0.0/10"):
        raise ValueError("Tailscale must report a valid shared-range IPv4 address")
    return str(ip)


def decode_config(source):
    """Refuse duplicate JSON keys before a native CLI rewrite could drop state."""

    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate Paseo config JSON key")
            result[key] = value
        return result

    return json.loads(source, object_pairs_hook=no_duplicates)


def same_config(actual, expected):
    """Compare JSON values without Python's bool/int/float equality coercion."""
    return json.dumps(
        actual, sort_keys=True, separators=(",", ":"), allow_nan=False
    ) == json.dumps(
        expected, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def checked_config(home):
    if home.is_symlink() or home.parent.is_symlink():
        raise ValueError("refusing symlink Paseo home")
    path = home / "config.json"
    if path.is_symlink() or not path.is_file():
        raise ValueError("existing regular Paseo config.json is required")
    info = path.stat()
    if (
        info.st_uid != os.getuid()
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise ValueError("Paseo config must be exclusively owned by the user")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Change Paseo listener")
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview without modifying live state"
    )
    args = parser.parse_args(argv)
    try:
        tailscale, paseo = shutil.which("tailscale"), shutil.which("paseo")
        if not tailscale or not paseo:
            raise ValueError("tailscale and paseo CLIs must be installed")
        address = subprocess.run(  # nosec B603 - resolved Tailscale binary; fixed args.
            [tailscale, "ip", "-4"],
            check=True,
            text=True,
            capture_output=True,
            timeout=15,
        ).stdout.strip()
        listen = f"{tailnet_ip(address)}:6767"
        home = Path(
            os.environ.get("PASEO_HOME", str(Path.home() / ".paseo"))
        ).expanduser()
        path = checked_config(home)
        original = decode_config(path.read_text(encoding="utf-8"))
        verdict = validate_config(original, listen)
        if not args.apply or args.dry_run:
            print(f"Paseo tailnet listener: {verdict}; desired {listen}")
            return 0
        if os.environ.get(GATE, "0") != "1":
            raise ValueError(f"explicit apply requires {GATE}=1")
        if os.environ.get("PASEO_LISTEN"):
            raise ValueError(
                "PASEO_LISTEN overrides the config; refuse ambiguous deployment"
            )
        if os.environ.get("PASEO_RELAY_ENABLED", "").lower() not in ("", "0", "false"):
            raise ValueError(
                "PASEO_RELAY_ENABLED can override the private relay policy"
            )
        if verdict == "unchanged":
            print("Paseo tailnet listener already matches; restart if not yet applied")
            return 0
        # A private, isolated copy detects lossy Paseo schema normalisation before touching live state.
        import tempfile

        with tempfile.TemporaryDirectory(prefix="dotfiles-paseo-preflight-") as scratch:
            scratch_home = Path(scratch)
            scratch_config = scratch_home / "config.json"
            scratch_config.write_text(
                path.read_text(encoding="utf-8"), encoding="utf-8"
            )
            scratch_config.chmod(0o600)
            command = [
                paseo,
                "daemon",
                "config",
                "set",
                "daemon.listen",
                listen,
                "--home",
            ]
            subprocess.run(  # nosec B603 - resolved Paseo CLI and owner-only isolated preflight.
                command + [str(scratch_home)], check=True, timeout=30
            )
            preview = decode_config(scratch_config.read_text(encoding="utf-8"))
            intended = copy.deepcopy(original)
            intended.setdefault("daemon", {})["listen"] = listen
            if not same_config(preview, intended):
                raise ValueError(
                    "Paseo CLI would rewrite unrelated config; refusing live write"
                )
        if not same_config(decode_config(path.read_text(encoding="utf-8")), original):
            raise ValueError("Paseo config changed during preflight; retry")
        subprocess.run(  # nosec B603 - resolved Paseo binary, validated arguments.
            [
                paseo,
                "daemon",
                "config",
                "set",
                "daemon.listen",
                listen,
                "--home",
                str(home),
            ],
            check=True,
            timeout=30,
        )
        updated = decode_config(path.read_text(encoding="utf-8"))
        expected = copy.deepcopy(original)
        expected.setdefault("daemon", {})["listen"] = listen
        if not same_config(updated, expected) or validate_config(updated, listen) != "unchanged":
            raise ValueError(
                "Paseo config write changed more than the intended listener"
            )
        print("Paseo listener saved; restart daemon and verify mobile authentication")
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"Paseo tailnet setup refused: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
