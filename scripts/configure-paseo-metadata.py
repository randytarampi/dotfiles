#!/usr/bin/env python3
"""Owner-only Pi-local metadata provider registration in Paseo."""

import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess  # nosec B404 - fixed native Paseo configuration command.
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
from cli_helpers import add_common_args  # noqa: E402
from discover_models import list_local_ollama_models  # noqa: E402
from paseo_metadata import (  # noqa: E402
    metadata_changes,
    private_json,
    resolve_orchestrator,
)
import tier_registry  # noqa: E402

GATE = "DOTFILES_RUN_PASEO_METADATA_SETUP"


def helpers():
    spec = importlib.util.spec_from_file_location(
        "paseo_metadata_hermes_helpers", HERE / "configure-paseo-hermes.py"
    )
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def native_set(paseo, home, providers):
    try:
        result = subprocess.run(  # nosec B603 - fixed native config args.
            [
                paseo,
                "daemon",
                "config",
                "set",
                "agents.metadataGeneration.providers",
                json.dumps(providers, separators=(",", ":")),
                "--home",
                str(home),
            ],
            capture_output=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired:
        # Do not disclose subprocess output or roll back an uncertain live write.
        raise ValueError("Paseo metadata native setter timed out") from None
    if result.returncode:
        # A failed command may already have changed the native configuration.
        # Refuse success even when the subsequent JSON readback looks correct.
        raise ValueError(
            f"Paseo metadata native setter failed (exit {result.returncode})"
        )


def apply(paseo, home, path, original, desired, helper):
    expected = copy.deepcopy(original)
    expected.setdefault("agents", {}).setdefault("metadataGeneration", {})[
        "providers"
    ] = desired
    with tempfile.TemporaryDirectory(prefix="paseo-metadata-stage-") as scratch:
        target_home = Path(scratch)
        target = target_home / "config.json"
        target.write_bytes(path.read_bytes())
        target.chmod(0o600)
        native_set(paseo, target_home, desired)
        staged = helper.decode_config(target.read_text(encoding="utf-8"))
        if not helper.same_config(staged, expected):
            raise ValueError("Paseo metadata setter would change unrelated config")
    if not helper.same_config(
        helper.decode_config(path.read_text(encoding="utf-8")), original
    ):
        raise ValueError("Paseo config changed during preflight")
    native_set(paseo, home, desired)
    latest = helper.decode_config(path.read_text(encoding="utf-8"))
    if not helper.same_config(latest, expected):
        raise ValueError("unexpected native config change; no snapshot rollback")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    add_common_args(parser)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--preset", required=True)
    args = parser.parse_args(argv)
    try:
        if os.environ.get(GATE, "0") != "1" and args.apply and not args.dry_run:
            raise ValueError("explicit metadata apply requires " + GATE + "=1")
        helper = helpers()
        paseo = shutil.which("paseo")
        if not paseo or not shutil.which("pi"):
            raise ValueError("installed Paseo and Pi CLIs required")
        pi_home = Path(
            os.environ.get("PI_CODING_AGENT_DIR", "~/.pi/agent")
        ).expanduser()
        settings = private_json(pi_home / "settings.json")
        models = private_json(pi_home / "models.json")
        registry = tier_registry.load_registry()
        selected = resolve_orchestrator(
            registry,
            args.preset,
            list_local_ollama_models(),
            pi_settings=settings,
            pi_models=models,
        )
        home = Path(
            os.environ.get("PASEO_HOME", str(Path.home() / ".paseo"))
        ).expanduser()
        path = helper.checked_config(home)
        original = helper.decode_config(path.read_text(encoding="utf-8"))
        changes = metadata_changes(original, selected)
        print("Proposed Pi metadata model: " + selected)
        if not changes or not args.apply or args.dry_run:
            return 0
        apply(paseo, home, path, original, changes, helper)
        print("Paseo manual metadata preference saved; restart the daemon")
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print("Paseo local metadata refused: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
