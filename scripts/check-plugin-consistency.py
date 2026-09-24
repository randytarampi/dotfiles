#!/usr/bin/env python3
"""Check pinned OpenCode plugins across install and generator sources."""

import argparse
import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SCRIPT = (
    REPO_ROOT / ".chezmoiscripts/run_onchange_07-install-opencode-plugins.sh.tmpl"
)
CONFIG_SCRIPT = REPO_ROOT / "scripts/configure-opencode.py"
DCP_SCRIPT = REPO_ROOT / "scripts/configure-opencode-dcp.py"
PLUGIN_LINE = re.compile(r'^\s*["\']([^"\']+)["\']\s*,?\s*$')
EXPECTED = {
    "oh-my-opencode-slim@2.2.24",
    "@tarquinen/opencode-dcp@3.2.0",
    "@plannotator/opencode@0.27.18",
    "opencode-planning-with-files@1.0.1",
    "@slkiser/opencode-quota@4.10.2",
}


def parse_install_plugins(path):
    plugins, in_array = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not in_array and re.match(r"^\s*PLUGINS\s*=\s*\(\s*$", line):
            in_array = True
        elif in_array and re.match(r"^\s*\)\s*$", line):
            in_array = False
        elif in_array and (match := PLUGIN_LINE.match(line)):
            plugins.append(match.group(1))
    if in_array:
        raise ValueError("PLUGINS array is not terminated")
    return plugins


def _plugin_value(value):
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    if isinstance(value, (ast.List, ast.Tuple)) and value.elts:
        return _plugin_value(value.elts[0])
    return None


def parse_config_plugins(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    plugins = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "plugin":
                    plugins.extend(filter(None, (_plugin_value(e) for e in value.elts)))
    return plugins


def parse_cli_plugins(path):
    text = path.read_text(encoding="utf-8")
    return set(re.findall(r'"package":\s*"([^"]+)"', text)) | set(
        re.findall(r'"@[^" ]+":\s*"([^" ]+)"', text)
    )


def main():
    argparse.ArgumentParser(description=__doc__, allow_abbrev=False).parse_args()
    try:
        install = set(parse_install_plugins(INSTALL_SCRIPT))
        config = set(parse_config_plugins(CONFIG_SCRIPT))
        cli = parse_cli_plugins(DCP_SCRIPT)
        for label, actual in (("install", install), ("config", config)):
            missing = EXPECTED - actual
            if missing:
                print(f"ERROR: {label} missing: {', '.join(sorted(missing))}")
                return 1
        required_cli = {
            "@tarquinen/opencode-dcp@3.2.0",
            "@slkiser/opencode-quota@4.10.2",
        }
        if not required_cli <= cli:
            print(
                f"ERROR: cli.json writer missing: {', '.join(sorted(required_cli - cli))}"
            )
            return 1
        if install != config | {
            "opencode-planning-with-files@1.0.1",
            "@slkiser/opencode-quota@4.10.2",
        }:
            print(
                f"ERROR: install/config plugin mismatch: install={sorted(install)}, config={sorted(config)}"
            )
            return 1
        print("OpenCode plugin sources are consistent.")
        return 0
    except (OSError, SyntaxError, ValueError) as error:
        print(f"ERROR: could not check plugin consistency: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
