#!/usr/bin/env python3
"""Check OpenCode plugin specifications across install and config sources."""

import argparse
import ast
import re
import sys
from pathlib import Path

from lib.cli_helpers import add_common_args

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SCRIPT = (
    REPO_ROOT / ".chezmoiscripts/run_onchange_07-install-opencode-plugins.sh.tmpl"
)
CONFIG_SCRIPT = REPO_ROOT / "scripts/configure-opencode.py"
PLUGIN_LINE = re.compile(r'^\s*["\']([^"\']+)["\']\s*,?\s*$')


def parse_install_plugins(path):
    """Return plugin specs installed by the shell template."""
    lines = path.read_text(encoding="utf-8").splitlines()
    plugins = []
    in_array = False
    for line in lines:
        if not in_array and re.match(r"^\s*PLUGINS\s*=\s*\(\s*$", line):
            in_array = True
            continue
        if in_array:
            if re.match(r"^\s*\)\s*$", line):
                in_array = False
                continue
            match = PLUGIN_LINE.match(line)
            if match:
                plugins.append(match.group(1))

        if re.search(r"\b(?:bunx|npx)\s+oh-my-opencode-slim@latest\s+install\b", line):
            plugins.append("oh-my-opencode-slim@latest")
    if in_array:
        raise ValueError("PLUGINS array is not terminated")
    return plugins


def _plugin_value(value):
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    if isinstance(value, (ast.List, ast.Tuple)) and value.elts:
        first = value.elts[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
    return None


def parse_config_plugins(path):
    """Return plugin specs from Python dictionary entries named ``plugin``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    plugins = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value == "plugin":
                if not isinstance(value, ast.List):
                    raise ValueError("config plugin entry is not a list")
                for entry in value.elts:
                    plugin = _plugin_value(entry)
                    if plugin is not None:
                        plugins.append(plugin)
    if not plugins:
        raise ValueError('config generator contains no "plugin" entries')
    return plugins


def plugin_base(spec):
    return spec[:-6] if spec.endswith("@latest") else spec


def check_consistency(install_plugins, config_plugins):
    install_by_base = {plugin_base(spec): spec for spec in install_plugins}
    config_by_base = {plugin_base(spec): spec for spec in config_plugins}
    exit_code = 0

    for name in sorted(set(install_by_base) & set(config_by_base)):
        install_spec = install_by_base[name]
        config_spec = config_by_base[name]
        if install_spec != config_spec:
            print(
                f"ERROR: spec-string mismatch for {name}: "
                f"install={install_spec!r}, config={config_spec!r}"
            )
            exit_code = 1

    for name in sorted(set(install_by_base) - set(config_by_base)):
        print(f"WARNING: installed but not configured: {install_by_base[name]}")
    for name in sorted(set(config_by_base) - set(install_by_base)):
        print(f"WARNING: configured but not installed: {config_by_base[name]}")
    return exit_code


def main():
    parser = argparse.ArgumentParser(
        description="Check OpenCode plugin specs in install and config sources."
    )
    add_common_args(parser)
    parser.parse_args()

    try:
        install_plugins = parse_install_plugins(INSTALL_SCRIPT)
        config_plugins = parse_config_plugins(CONFIG_SCRIPT)
        return check_consistency(install_plugins, config_plugins)
    except (OSError, SyntaxError, ValueError) as error:
        print(f"ERROR: could not check plugin consistency: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
