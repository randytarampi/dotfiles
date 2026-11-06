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

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
from cli_helpers import add_common_args  # noqa: E402
from paseo_provider_write import apply_document  # noqa: E402

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


def apply_entries(paseo, home, source, config, updates, helpers):
    """Preserve all user providers while applying only approved ACP additions."""
    expected = copy.deepcopy(config)
    agents = expected.get("agents", {})
    if not isinstance(agents, dict):
        raise ValueError("agents must be an object")
    providers = copy.deepcopy(agents.get("providers", {}))
    if not isinstance(providers, dict):
        raise ValueError("agents.providers must be an object")
    for name, value in updates.items():
        providers[name] = value
    expected.setdefault("agents", {})["providers"] = providers
    apply_document(
        paseo,
        home,
        source,
        config,
        expected,
        helpers.decode_config,
        helpers.same_config,
        timeout=60,
    )


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
