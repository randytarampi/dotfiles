#!/usr/bin/env python3
"""Validate the canonical OpenCode plugin manifest and installed cache."""

import argparse
import ast
import json
import os
import re
import sys
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from opencode_plugins import active_plugin_specs, is_pinned_spec, load_plugin_manifest

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SCRIPT = (
    REPO_ROOT / ".chezmoiscripts/run_onchange_07-install-opencode-plugins.sh.tmpl"
)
CONFIG_SCRIPT = REPO_ROOT / "scripts/configure-opencode.py"
MANIFEST = REPO_ROOT / "configs/opencode/opencode-plugins.json"


def load_manifest(path=MANIFEST):
    return load_plugin_manifest(path)


def parse_install_plugins(path, manifest_path=MANIFEST):
    """Return the manifest entries consumed by the install template.

    The template intentionally reads JSON at render/runtime rather than
    copying package strings into shell. This parser verifies that reference
    and treats the manifest as the template's generated list.
    """
    text = Path(path).read_text(encoding="utf-8")
    if (
        "from opencode_plugins import active_plugin_specs" not in text
        or "PLUGIN_SPECS" not in text
    ):
        raise ValueError("install template does not consume the canonical manifest")
    if 'OH_MY_PLUGIN="${PLUGIN_SPECS[0]}"' not in text:
        raise ValueError(
            "install template does not use the manifest's dedicated installer entry"
        )
    loop = re.search(
        r'for plugin in "\$\{PLUGIN_SPECS\[@\]\}"; do(?P<body>.*?)done',
        text,
        re.DOTALL,
    )
    if not loop:
        raise ValueError("install template has no manifest append loop")
    actual_guard = " ".join(loop.group("body").split())
    expected_guard = '[[ "$plugin" == "$OH_MY_PLUGIN" ]] || PLUGINS+=("$plugin")'
    if actual_guard != expected_guard:
        raise ValueError(
            "install template manifest exclusion condition changed: "
            f"{actual_guard!r}"
        )
    # Evaluate the literal PLUGINS array and the manifest-backed append loop.
    block = text.split("PLUGINS=(", 1)[1].split(")", 1)[0]
    literals = []
    for line in block.splitlines():
        value = line.strip().strip('"')
        if value.startswith("@") or value.startswith("opencode-"):
            literals.append(value)
    specs = active_plugin_specs(manifest_path)
    return [specs[0]] + literals + specs[1:]


def _plugin_value(value):
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    if isinstance(value, (ast.List, ast.Tuple)) and value.elts:
        return _plugin_value(value.elts[0])
    return None


def parse_config_plugins(path):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"), filename=str(path))
    manifest_plugins = active_plugin_specs()
    environment = {
        "configured_plugins": manifest_plugins,
        "plannotator": next(
            spec
            for spec in manifest_plugins
            if spec.startswith("@plannotator/opencode@")
        ),
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value == "plugin":
                if not isinstance(value, ast.List):
                    raise ValueError("config plugin entry is not a list")
                # The global config is the only plugin list with lsp=True;
                # project mode intentionally emits an empty list.
                if not any(
                    isinstance(key, ast.Constant)
                    and key.value == "lsp"
                    and isinstance(val, ast.Constant)
                    and val.value is True
                    for key, val in zip(node.keys, node.values)
                ):
                    continue
                return _evaluate_plugin_list(value, environment)
    raise ValueError('config generator contains no "plugin" entry')


def _config_consumes_manifest(path=CONFIG_SCRIPT):
    text = Path(path).read_text(encoding="utf-8")
    return (
        "from opencode_plugins import active_plugin_specs" in text
        and "configured_plugins" in text
    )


def _evaluate_plugin_list(node, environment):
    result = []
    for item in node.elts:
        if isinstance(item, ast.Starred):
            result.extend(_evaluate_expression(item.value, environment))
        else:
            value = _evaluate_expression(item, environment)
            result.append(value[0] if isinstance(value, list) else value)
    return result


def _evaluate_expression(node, environment):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return environment[node.id]
    if isinstance(node, ast.Subscript):
        value = _evaluate_expression(node.value, environment)
        if isinstance(node.slice, ast.Slice):
            lower = node.slice.lower.value if node.slice.lower else None
            upper = node.slice.upper.value if node.slice.upper else None
            return value[lower:upper]
        return value[node.slice.value]
    if isinstance(node, (ast.List, ast.Tuple)):
        return _evaluate_plugin_list(node, environment)
    if isinstance(node, ast.Dict):
        return None
    raise ValueError(f"unsupported plugin expression: {ast.dump(node)}")


def plugin_name_version(spec):
    name, version = spec.rsplit("@", 1)
    return name, version


def is_plugin_installed(spec, cache_dir=None):
    """Read-only check matching OpenCode's npm cache installation mechanism."""
    cache = Path(
        cache_dir or os.environ.get("OPENCODE_CACHE", Path.home() / ".cache/opencode")
    )
    name, version = plugin_name_version(spec)
    expected_dir = name.replace("/", os.sep) + "@" + version
    for root in (cache / "npm", cache / "packages"):
        if (root / expected_dir).exists():
            return True
    if not cache.exists():
        return False
    for package_json in cache.rglob("package.json"):
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for section in ("dependencies", "devDependencies", "peerDependencies"):
            if data.get(section, {}).get(name) == version:
                return True
    return False


def check_consistency(
    manifest_plugins, install_plugins, config_plugins, installed=None
):
    expected = list(manifest_plugins)
    expected.insert(1, "@tarquinen/opencode-dcp@latest")
    exit_code = 0
    for label, actual in (
        ("install template", install_plugins),
        ("config generator", config_plugins),
    ):
        if actual != expected:
            print(f"ERROR: {label} does not equal canonical manifest")
            print(f"  expected={expected!r}\n  actual={actual!r}")
            exit_code = 1
    if installed is not None:
        for spec, present in installed.items():
            if not present:
                print(f"ERROR: manifest plugin is not installed: {spec}")
                exit_code = 1
    return exit_code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-installed", action="store_true")
    parser.add_argument("--cache-dir", type=Path, default=None)
    args = parser.parse_args()
    try:
        load_manifest()  # Validate the complete canonical manifest, including gated entries.
        manifest_plugins = active_plugin_specs()
        if not _config_consumes_manifest():
            raise ValueError("config generator does not consume the canonical manifest")
        install_plugins = parse_install_plugins(INSTALL_SCRIPT)
        config_plugins = parse_config_plugins(CONFIG_SCRIPT)
        installed = None
        if (
            args.check_installed
            or os.environ.get("DOTFILES_CHECK_PLUGIN_INSTALL") == "1"
        ):
            installed = {
                spec: is_plugin_installed(spec, args.cache_dir)
                for spec in manifest_plugins
            }
        return check_consistency(
            manifest_plugins, install_plugins, config_plugins, installed
        )
    except (OSError, SyntaxError, ValueError) as error:
        print(f"ERROR: could not check plugin consistency: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
