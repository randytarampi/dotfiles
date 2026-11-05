#!/usr/bin/env python3
"""Owner-only Paseo MCP server activation; injection into agents remains off."""

import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess  # nosec B404 - fixed native Paseo argv; no shell.
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
from cli_helpers import add_common_args  # noqa: E402
from paseo_web_policy import require_password, valid_daemon  # noqa: E402

GATE = "DOTFILES_RUN_PASEO_MCP_SETUP"


def load_helpers():
    spec = importlib.util.spec_from_file_location(
        "paseo_hermes_mcp_helpers", HERE / "configure-paseo-hermes.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def plan(config):
    """Require a nonconflicting existing config; add only safe MCP defaults."""
    if not isinstance(config, dict):
        raise ValueError("Paseo config must be an object")
    daemon = config.get("daemon", {})
    if not isinstance(daemon, dict):
        raise ValueError("daemon must be an object")
    mcp = daemon.get("mcp", {})
    if not isinstance(mcp, dict):
        raise ValueError("daemon.mcp must be an object")
    if mcp.get("injectIntoAgents") is True:
        raise ValueError("agent MCP injection already enabled; owner review required")
    if "enabled" in mcp and mcp["enabled"] is not True:
        raise ValueError("user-owned MCP-disabled setting cannot be overwritten")
    if "injectIntoAgents" in mcp and mcp["injectIntoAgents"] is not False:
        raise ValueError("unexpected MCP injection state")
    changes = []
    if "enabled" not in mcp:
        changes.append(("daemon.mcp.enabled", True))
    if "injectIntoAgents" not in mcp:
        changes.append(("daemon.mcp.injectIntoAgents", False))
    return changes


def set_expected(config, name, value):
    config.setdefault("daemon", {}).setdefault("mcp", {})[
        name.removeprefix("daemon.mcp.")
    ] = value


def native_set(paseo, home, name, value):
    return subprocess.run(  # nosec B603 - resolved executable, fixed CLI arguments.
        [
            paseo,
            "daemon",
            "config",
            "set",
            name,
            json.dumps(value),
            "--home",
            str(home),
        ],
        capture_output=True,
        timeout=45,
        check=False,
    ).returncode


def apply(paseo, home, source, original, changes, helpers):
    with tempfile.TemporaryDirectory(prefix="paseo-mcp-stage-") as scratch:
        staged = Path(scratch)
        config_path = staged / "config.json"
        config_path.write_bytes(source.read_bytes())
        config_path.chmod(0o600)
        expected = copy.deepcopy(original)
        for name, value in changes:
            set_expected(expected, name, value)
            native_set(paseo, staged, name, value)
            saved = helpers.decode_config(config_path.read_text(encoding="utf-8"))
            if not helpers.same_config(saved, expected):
                raise ValueError("Paseo MCP staging changed unrelated config state")
    initial = helpers.decode_config(source.read_text(encoding="utf-8"))
    if not helpers.same_config(initial, original):
        raise ValueError("Paseo config changed during staging")
    expected = copy.deepcopy(original)
    for name, value in changes:
        set_expected(expected, name, value)
        native_set(paseo, home, name, value)
        saved = helpers.decode_config(source.read_text(encoding="utf-8"))
        if not helpers.same_config(saved, expected):
            raise ValueError("Paseo config changed during native write; no rollback")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    add_common_args(parser)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        paseo = shutil.which("paseo")
        if not paseo:
            raise ValueError("Paseo executable unavailable")
        helpers = load_helpers()
        home = Path(
            os.environ.get("PASEO_HOME", str(Path.home() / ".paseo"))
        ).expanduser()
        source = helpers.checked_config(home)
        original = helpers.decode_config(source.read_text(encoding="utf-8"))
        daemon = valid_daemon(original)
        require_password(daemon)
        changes = plan(original)
        print("Paseo MCP changes: " + (", ".join(n for n, _ in changes) or "none"))
        if not args.apply or args.dry_run or not changes:
            return 0
        if os.environ.get(GATE) != "1":
            raise ValueError("explicit owner-only apply requires " + GATE + "=1")
        apply(paseo, home, source, original, changes, helpers)
        print("Paseo MCP enabled with agent injection disabled; restart required")
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print("Paseo MCP refused: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
