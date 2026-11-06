#!/usr/bin/env python3
"""Opt-in Hermes ACP registration in Paseo without a messaging gateway."""

import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess  # nosec B404 - resolved Hermes/Paseo commands, no shell.
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from cli_helpers import add_common_args  # noqa: E402 -- local bootstrap above.
import tempfile

GATE = "DOTFILES_RUN_PASEO_HERMES_ACP_SETUP"


def native_set(paseo, home, providers):
    result = subprocess.run(  # nosec B603 - resolved Paseo binary, fixed path.
        [
            paseo,
            "daemon",
            "config",
            "set",
            "agents.providers",
            json.dumps(providers, separators=(",", ":")),
            "--home",
            str(home),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        reason = f"{result.stdout or ''}\n{result.stderr or ''}"
        reason = reason.replace("/Users/randy.tarampi", "<user>")
        reason = reason.replace(str(home), "<home>")
        reason = " ".join(reason.split())[:300]
        raise ValueError(
            f"Paseo config setter failed (exit {result.returncode})"
            + (f": {reason}" if reason else "")
        )


def provider(hermes_binary):
    """Use an absolute executable so the Paseo daemon needs no shell PATH."""
    return {
        "extends": "acp",
        "label": "Hermes",
        "command": [os.path.abspath(hermes_binary), "acp"],
    }


def classify(data, expected):
    if not isinstance(data, dict):
        raise ValueError("Paseo config must be a JSON object")
    agents = data.get("agents", {})
    if not isinstance(agents, dict):
        raise ValueError("agents must be an object")
    providers = agents.get("providers", {})
    if not isinstance(providers, dict):
        raise ValueError("agents.providers must be an object")
    if "hermes" not in providers:
        return "absent"
    if providers["hermes"] == expected:
        return "unchanged"
    raise ValueError("user-owned agents.providers.hermes differs; refusing replacement")


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
    ) == json.dumps(expected, sort_keys=True, separators=(",", ":"), allow_nan=False)


def checked_config(home):
    if home.is_symlink() or home.parent.is_symlink():
        raise ValueError("refusing symlink Paseo home")
    path = home / "config.json"
    if path.is_symlink() or not path.is_file():
        raise ValueError("Paseo config.json must exist as a regular file")
    info = path.stat()
    if (
        info.st_uid != os.getuid()
        or info.st_nlink != 1
        or not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise ValueError("Paseo config is not exclusively owned by this user")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--apply", action="store_true", help="Register Hermes ACP")
    add_common_args(parser)
    args = parser.parse_args(argv)
    try:
        paseo, hermes = shutil.which("paseo"), shutil.which("hermes")
        if not paseo or not hermes:
            raise ValueError("Paseo and Hermes CLIs must both be installed")
        desired = provider(hermes)
        home = Path(
            os.environ.get("PASEO_HOME", str(Path.home() / ".paseo"))
        ).expanduser()
        path = checked_config(home)
        original = decode_config(path.read_text(encoding="utf-8"))
        verdict = classify(original, desired)
        if not args.apply or args.dry_run:
            print(f"Hermes ACP provider: {verdict}; no changes made")
            return 0
        if os.environ.get(GATE, "0") != "1":
            raise ValueError(f"explicit apply requires {GATE}=1")
        try:
            subprocess.run(  # nosec B603 - resolved Hermes binary, fixed ACP preflight.
                [hermes, "acp", "--check"],
                check=True,
                timeout=30,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as error:
            # No raw child output: it may contain credentials or private paths.
            raise ValueError(
                f"Hermes ACP preflight failed (exit {error.returncode}); run hermes acp --check locally"
            ) from None
        if verdict == "unchanged":
            print("Hermes ACP already registered and preflight passed")
            return 0
        expected = copy.deepcopy(original)
        providers = copy.deepcopy(original.get("agents", {}).get("providers", {}))
        if not isinstance(providers, dict):
            raise ValueError("agents.providers must be an object")
        providers["hermes"] = desired
        expected.setdefault("agents", {})["providers"] = providers
        # The native Paseo CLI may normalize or discard unrelated user-owned
        # keys. Prove the exact mutation in a private copy before any live write.
        with tempfile.TemporaryDirectory(prefix="dotfiles-paseo-hermes-") as scratch:
            staged_home = Path(scratch)
            staged_config = staged_home / "config.json"
            staged_config.write_bytes(path.read_bytes())
            staged_config.chmod(0o600)
            native_set(paseo, staged_home, providers)
            preview = decode_config(staged_config.read_text(encoding="utf-8"))
            if not same_config(preview, expected):
                raise ValueError(
                    "Paseo CLI would rewrite unrelated config; refusing live write"
                )
        if not same_config(decode_config(path.read_text(encoding="utf-8")), original):
            raise ValueError("Paseo config changed during preflight; retry")
        native_set(paseo, home, providers)
        updated = decode_config(path.read_text(encoding="utf-8"))
        if (
            not same_config(updated, expected)
            or classify(updated, desired) != "unchanged"
        ):
            raise ValueError("Paseo config write changed more than Hermes ACP provider")
        print("Hermes ACP registered; validate a real local session in Paseo")
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"Hermes ACP registration refused: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
