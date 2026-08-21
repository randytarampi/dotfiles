#!/usr/bin/env python3
"""Validate fleet-registry.json against actual repo state."""

import argparse
import json
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent


def guidance_path_is_listed(guidance_text, guidance_path):
    """Check a registry path against the configure script's AGENT_FILES list."""
    relative_path = guidance_path.removeprefix("~/")
    parts = relative_path.split("/")
    search = '"' + '", "'.join(parts) + '"'
    if search in guidance_text:
        return True
    # Junie is resolved through JUNIE_DIR so its literal ~/.junie path is not
    # present in the AGENT_FILES source.
    return guidance_path == "~/.junie/AGENTS.md" and "JUNIE_DIR" in guidance_text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    registry_path = SCRIPT_DIR / "lib" / "fleet-registry.json"
    env_example_path = ROOT / "dot_dotfiles" / "shell" / ".env.example"
    guidance_script_path = SCRIPT_DIR / "configure-agent-guidance.py"

    try:
        with registry_path.open(encoding="utf-8") as registry_file:
            registry = json.load(registry_file)
        env_text = env_example_path.read_text(encoding="utf-8")
        guidance_text = guidance_script_path.read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError) as error:
        print(f"Fleet coverage check failed: {error}", file=sys.stderr)
        return 1

    errors = []
    for tool_name, tool_info in registry.get("tools", {}).items():
        for env_var in tool_info.get("telemetry_env_vars", []):
            pattern = re.compile(
                rf"^\s*(?:export\s+)?{re.escape(env_var)}\s*=", re.MULTILINE
            )
            if not pattern.search(env_text):
                errors.append(
                    f"{tool_name}: env var '{env_var}' not found "
                    "(uncommented) in .env.example"
                )

        guidance_path = tool_info.get("guidance_path", "")
        if tool_info.get("guidance_consumes", False) and not guidance_path:
            errors.append(f"{tool_name}: guidance_consumes=true but no guidance_path")
        elif guidance_path and not guidance_path_is_listed(
            guidance_text, guidance_path
        ):
            errors.append(
                f"{tool_name}: guidance path '{guidance_path}' not found in "
                "configure-agent-guidance.py AGENT_FILES"
            )

    if errors:
        print("Fleet coverage check failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print(f"Fleet coverage OK: {len(registry.get('tools', {}))} tools verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
