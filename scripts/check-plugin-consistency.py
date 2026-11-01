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

from opencode_plugins import (  # noqa: E402  # sys.path bootstrap above is intentional.
    active_plugin_specs,
    cli_plugin_specs,
    configured_plugin_specs,
    load_plugin_manifest,
)

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
    # The template's PLUGINS array starts empty and must be mutated only by
    # the guarded manifest-driven loop above: any other PLUGINS+= site would
    # let an extra install bypass the manifest (decisive against overrides).
    append_sites = [line for line in text.splitlines() if "PLUGINS+=(" in line]
    if len(append_sites) != 1 or append_sites[0].strip() != expected_guard:
        raise ValueError(
            "install template PLUGINS array deviates from the guarded "
            "manifest-driven loop (unauthorized PLUGINS append)"
        )
    return active_plugin_specs(manifest_path)


def parse_install_cli_plugins(path, manifest_path=MANIFEST):
    text = Path(path).read_text(encoding="utf-8")
    if (
        "from opencode_plugins import cli_plugin_specs" not in text
        or "CLI_PLUGIN_SPECS" not in text
    ):
        raise ValueError("install template does not consume CLI plugin manifest")
    if 'opencode plugin add "${plugin}"' not in text:
        raise ValueError("install template does not add CLI plugins")
    cli_sites = [line for line in text.splitlines() if "CLI_PLUGIN_SPECS+=(" in line]
    if len(cli_sites) != 1 or cli_sites[0].strip() != 'CLI_PLUGIN_SPECS+=("$plugin")':
        raise ValueError(
            "install template CLI_PLUGIN_SPECS array deviates from the "
            "manifest-driven loop (unauthorized CLI append)"
        )
    return cli_plugin_specs(manifest_path)


def parse_voice_writer_source(path):
    """Assert the voice writer's cli.json registration is manifest-driven."""
    source = Path(path).read_text(encoding="utf-8")
    if "from opencode_plugins import" not in source or (
        "active_cli_plugin_specs" not in source
    ):
        raise ValueError("voice writer does not consume the cli_plugins manifest")
    if "voice_specs = active_cli_plugin_specs()" not in source:
        raise ValueError("voice writer does not resolve its spec from the manifest")
    if "voice_specs[0]" not in source:
        raise ValueError("voice writer does not use the resolved manifest spec")
    return True


def check_cli_plugin_consistency(
    manifest_path=MANIFEST, install_path=INSTALL_SCRIPT, voice_path=None
):
    expected = cli_plugin_specs(manifest_path)
    if voice_path is None:
        voice_path = (
            INSTALL_SCRIPT.parents[1] / "scripts" / "configure-opencode-voice.py"
        )
    parse_voice_writer_source(voice_path)
    if parse_install_cli_plugins(install_path, manifest_path) != expected:
        print("ERROR: install template CLI plugins differ from manifest")
        return 1
    return 0


FORBIDDEN_SPEC_PREFIXES = (
    # Retired v1-era packages (one-way migration): the original TokenScope,
    # the original npm-line VibeGuard, and the removed Quota surfaces.
    "@ramtinj95/opencode-tokenscope@",
    "opencode-vibeguard@",
    "@slkiser/opencode-quota",
)


def validate_forbidden_specs(manifest_path=MANIFEST):
    manifest_text = Path(manifest_path).read_text(encoding="utf-8")
    for forbidden in FORBIDDEN_SPEC_PREFIXES:
        if forbidden in manifest_text:
            raise ValueError(f"forbidden legacy plugin spec found: {forbidden}")


def _evaluate_plugin_list(node, environment):
    result = []
    for item in node.elts:
        if isinstance(item, ast.Starred):
            result.extend(_evaluate_expression(item.value, environment))
        else:
            value = _evaluate_expression(item, environment)
            if isinstance(value, list):
                # A v1 [pkg, options] tuple normalizes to its package spec.
                if value[0] is not None:
                    result.append(value[0])
            elif value is not None:
                result.append(value)
    return result


<<<<<<< HEAD
def _evaluate_subscript(node, environment):
    value = _evaluate_expression(node.value, environment)
    if isinstance(node.slice, ast.Slice):
        lower = node.slice.lower.value if node.slice.lower else None
        upper = node.slice.upper.value if node.slice.upper else None
        return value[lower:upper]
    return value[node.slice.value]


def _evaluate_package_dict(node, environment):
    # A v2 {package, options} plugin object normalizes to its package
    # spec, matching the v1 tuple handling above.
    for key, value in zip(node.keys, node.values):
        if isinstance(key, ast.Constant) and key.value == "package":
            return _evaluate_expression(value, environment)
    return None
=======
def _evaluate_expression(node, environment):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return environment[node.id]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id == "active_plugin_specs":
            return active_plugin_specs()
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _evaluate_expression(node.left, environment) + _evaluate_expression(
            node.right, environment
        )
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
        # A v2 {package, options} plugin object normalizes to its package
        # spec, matching the v1 tuple handling above.
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value == "package":
                return _evaluate_expression(value, environment)
        return None
    raise ValueError(f"unsupported plugin expression: {ast.dump(node)}")
>>>>>>> 4ca973c2 (feat(scripts): emit native OpenCode v2 configuration)


def _evaluate_expression(node, environment):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return environment[node.id]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id == "active_plugin_specs":
            return active_plugin_specs()
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _evaluate_expression(node.left, environment) + _evaluate_expression(
            node.right, environment
        )
    if isinstance(node, ast.Subscript):
        return _evaluate_subscript(node, environment)
    if isinstance(node, (ast.List, ast.Tuple)):
        return _evaluate_plugin_list(node, environment)
    if isinstance(node, ast.Dict):
        return _evaluate_package_dict(node, environment)
    # The emitter derives the plugins array with a list comprehension whose
    # element is a conditional ({package, options} object for plannotator,
    # else the manifest spec). Support exactly that shape.
    if isinstance(node, ast.IfExp):
        if _evaluate_expression(node.test, environment):
            return _evaluate_expression(node.body, environment)
        return _evaluate_expression(node.orelse, environment)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        receiver = _evaluate_expression(node.func.value, environment)
        if (
            node.func.attr == "startswith"
            and isinstance(receiver, str)
            and len(node.args) == 1
        ):
            return receiver.startswith(_evaluate_expression(node.args[0], environment))
    if isinstance(node, ast.ListComp) and len(node.generators) == 1:
        (generator,) = node.generators
        if not isinstance(generator.target, ast.Name) or generator.ifs:
            raise ValueError("unsupported plugin comprehension")
        result = []
        for item in _evaluate_expression(generator.iter, environment):
            scope = dict(environment)
            scope[generator.target.id] = item
            result.append(_evaluate_expression(node.elt, scope))
        return result
    raise ValueError(f"unsupported plugin expression: {ast.dump(node)}")


def parse_config_plugins(path):
    source = Path(path).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    manifest_plugins = configured_plugin_specs()
    environment = {
        "configured_plugins": active_plugin_specs(),
        "plannotator": next(
            spec
            for spec in manifest_plugins
            if spec.startswith("@plannotator/opencode")
        ),
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value == "plugins":
                if not isinstance(value, ast.List):
                    raise ValueError("config plugins entry is not a list")
                plugins = _evaluate_plugin_list(value, environment)
                # The global config is the only plugin list with entries; the
                # project mode intentionally emits an empty list.
                if not plugins:
                    continue
                return plugins
    raise ValueError('config generator contains no "plugins" entry')


def _config_consumes_manifest(path=CONFIG_SCRIPT):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"), filename=str(path))
    imported_names = set()
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or node.module != "opencode_plugins":
            continue
        for alias in node.names:
            if alias.name == "active_plugin_specs":
                imported_names.add(alias.asname or alias.name)
    if not imported_names:
        return False
    return any(
        isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "configured_plugins"
            for target in node.targets
        )
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id in imported_names
        for node in ast.walk(tree)
    )


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
    if version == "latest":
        # The floating alias installs into an "<name>@latest" directory (the
        # dir check above); package.json files always record a concrete
        # version, so the equality fallback below can never match "latest".
        return False
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
    if any(spec.startswith("@ramtinj95/opencode-tokenscope@") for spec in expected):
        print("ERROR: forbidden legacy TokenScope plugin spec found")
        exit_code = 1
    if any(spec.startswith("opencode-vibeguard@") for spec in expected):
        print("ERROR: forbidden legacy VibeGuard plugin spec found")
        exit_code = 1
    if any(spec.startswith("@slkiser/opencode-quota") for spec in expected):
        print("ERROR: forbidden retired Quota plugin spec found")
        exit_code = 1
    return exit_code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-installed", action="store_true")
    parser.add_argument("--cache-dir", type=Path, default=None)
    args = parser.parse_args()
    if args.cache_dir is not None and not (
        args.check_installed or os.environ.get("DOTFILES_CHECK_PLUGIN_INSTALL") == "1"
    ):
        parser.error("--cache-dir requires --check-installed")
    try:
        validate_forbidden_specs()
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
        result = check_consistency(
            manifest_plugins, install_plugins, config_plugins, installed
        )
        return max(result, check_cli_plugin_consistency())
    except (OSError, SyntaxError, ValueError) as error:
        print(f"ERROR: could not check plugin consistency: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
