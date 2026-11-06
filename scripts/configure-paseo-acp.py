#!/usr/bin/env python3
"""Preview or explicitly register qualified ACP CLIs with an existing Paseo daemon.

Never run from make deploy. A preview does not mutate native Paseo settings.
Issue #173 requires every apply to run with other config writers quiesced.
"""

import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess  # nosec B404 - fixed Paseo CLI and curated ACP argv.
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
from cli_helpers import add_common_args  # noqa: E402

GATE = "DOTFILES_RUN_PASEO_ACP_SETUP"
# These command contracts exist in configure-acp-agents.py. Paseo already has
# native OpenCode, Pi, Claude, Codex and Antigravity; Hermes has a separate lane.
CANDIDATES = ("gemini", "junie", "cursor", "copilot", "cline", "cortex")


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def discovered(specs, which):
    """Return eligible providers and explicit exclusions without launching agents."""
    found, skipped = {}, {}
    for name in CANDIDATES:
        item = specs.get(name)
        binary = which(item["command"]) if item else None
        if not binary or not os.path.isabs(binary):
            skipped[name] = "installed ACP executable not found"
            continue
        args = list(item.get("args", []))
        if item.get("env"):
            skipped[name] = "ACP environment contract needs separate safe qualification"
            continue
        if name == "cortex":
            # Connection strings are never copied to persistent config or logs.
            skipped[name] = "Cortex connection needs a separate owner-approved profile"
            continue
        if any(not isinstance(arg, str) or "$" in arg for arg in args):
            skipped[name] = "command needs unresolved environment substitution"
            continue
        found[name] = {
            "extends": "acp",
            "label": name.title() if name != "junie" else "Junie",
            "command": [os.path.abspath(binary)] + args,
        }
    return found, skipped


def desired_entries(config, found, selected):
    """Refuse to overwrite diverging user-owned custom entries."""
    if not isinstance(config, dict):
        raise ValueError("Paseo config must be an object")
    agents = config.get("agents", {})
    if not isinstance(agents, dict):
        raise ValueError("agents is not an object")
    providers = agents.get("providers", {})
    if not isinstance(providers, dict):
        raise ValueError("agents.providers is not an object")
    requested = selected or tuple(found)
    if not requested:
        raise ValueError("no ACP binaries available")
    unknown = set(requested) - set(CANDIDATES)
    if unknown:
        raise ValueError("unknown ACP candidates: " + ", ".join(sorted(unknown)))
    missing = set(requested) - set(found)
    if missing:
        raise ValueError("ACP candidates not eligible: " + ", ".join(sorted(missing)))
    updates = {}
    for name in requested:
        if name in providers and providers[name] != found[name]:
            raise ValueError("user-owned ACP provider collision: " + name)
        if name not in providers:
            updates[name] = found[name]
    return updates


def run_set(binary, home, providers):
    result = subprocess.run(  # nosec B603 - resolved executable, fixed native CLI.
        [
            binary,
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
        timeout=60,
        check=False,
    )
    if result.returncode:
        # The native CLI may echo the entire providers JSON, including secrets.
        # Disclose only a fixed diagnostic rather than untrusted CLI output.
        output = str(result.stdout or "") + str(result.stderr or "")
        detail = (
            "Unknown configuration path: agents.providers"
            if "Unknown configuration path" in output
            else "native output suppressed to protect provider settings"
        )
        raise ValueError(
            f"Paseo config setter failed (exit {result.returncode}): {detail}"
        )
    # The CLI may save and report a reload error; readback determines success.
    return result.returncode


def apply_entries(paseo, home, source, config, updates, helpers):
    expected = copy.deepcopy(config)
    agents = expected.get("agents", {})
    if not isinstance(agents, dict):
        raise ValueError("agents must be an object")
    providers = copy.deepcopy(agents.get("providers", {}))
    if not isinstance(providers, dict):
        raise ValueError("agents.providers must be an object")
    for name, value in updates.items():
        providers[name] = value
    # Only agents.providers entries differ from the original document.
    expected.setdefault("agents", {})["providers"] = providers
    with tempfile.TemporaryDirectory(prefix="paseo-acp-preflight-") as scratch:
        staged_home = Path(scratch)
        target = staged_home / "config.json"
        target.write_bytes(source.read_bytes())
        target.chmod(0o600)
        run_set(paseo, staged_home, providers)
        preview = helpers.decode_config(target.read_text(encoding="utf-8"))
        if not helpers.same_config(preview, expected):
            raise ValueError("native CLI would alter unrelated Paseo settings")
    if not helpers.same_config(
        helpers.decode_config(source.read_text(encoding="utf-8")), config
    ):
        raise ValueError("Paseo config changed before apply")
    run_set(paseo, home, providers)
    current = helpers.decode_config(source.read_text(encoding="utf-8"))
    if not helpers.same_config(current, expected):
        raise ValueError("Paseo config changed unexpectedly; no rollback attempted")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    add_common_args(parser)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--agents", help="Comma-separated eligible ACP provider IDs")
    args = parser.parse_args(argv)
    try:
        specs = load_module("acp_registry", "configure-acp-agents.py").ACP_AGENTS
        helpers = load_module("paseo_hermes_helpers", "configure-paseo-hermes.py")
        paseo = shutil.which("paseo")
        found, skipped = discovered(specs, shutil.which)
        selected = tuple(v.strip() for v in (args.agents or "").split(",") if v.strip())
        if not paseo or not os.path.isabs(paseo):
            raise ValueError("Paseo CLI must resolve to an absolute executable")
        home = Path(
            os.environ.get("PASEO_HOME", str(Path.home() / ".paseo"))
        ).expanduser()
        source = helpers.checked_config(home)
        current = helpers.decode_config(source.read_text(encoding="utf-8"))
        changes = desired_entries(current, found, selected)
        print("ACP eligible: " + (", ".join(sorted(found)) or "none"))
        print("ACP excluded: " + (", ".join(sorted(skipped)) or "none"))
        print("ACP proposed: " + (", ".join(sorted(changes)) or "already registered"))
        if not args.apply or args.dry_run or not changes:
            return 0
        if os.environ.get(GATE, "0") != "1":
            raise ValueError("owner-only apply requires " + GATE + "=1")
        apply_entries(paseo, home, source, current, changes, helpers)
        print("ACP registered; run installed session/permission acceptance before use")
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print("Paseo ACP setup refused: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
