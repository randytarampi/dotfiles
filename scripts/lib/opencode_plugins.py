"""Load the canonical OpenCode plugin manifest."""

import json
import argparse
import os
import re
from pathlib import Path

MANIFEST_PATH = (
    Path(__file__).resolve().parents[2] / "configs/opencode/opencode-plugins.json"
)


def load_plugin_manifest(path=MANIFEST_PATH):
    with Path(path).open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    if manifest.get("version") != 1:
        raise ValueError("OpenCode plugin manifest version must be 1")
    if not isinstance(manifest.get("excluded"), dict):
        raise ValueError("OpenCode plugin manifest must contain an excluded map")
    plugins = manifest.get("plugins")
    if not isinstance(plugins, list) or not all(
        isinstance(item, str) for item in plugins
    ):
        raise ValueError("OpenCode plugin manifest must contain a string plugins list")
    if len(set(plugins)) != len(plugins):
        raise ValueError("OpenCode plugin manifest contains duplicate entries")
    gated = manifest.get("gated", {})
    if not isinstance(gated, dict) or not set(gated).issubset(plugins):
        raise ValueError(
            "OpenCode plugin manifest gated entries must be declared plugins"
        )
    if any(
        not re.fullmatch(r"DOTFILES_RUN_[A-Z0-9_]+", gate) for gate in gated.values()
    ):
        raise ValueError("OpenCode plugin manifest gate names are invalid")
    if any(not is_pinned_spec(item) for item in plugins):
        raise ValueError(
            "OpenCode plugin manifest entries must be pinned package@version specs"
        )
    return manifest


def plugin_specs(path=MANIFEST_PATH):
    return load_plugin_manifest(path)["plugins"]


def is_pinned_spec(spec: str) -> bool:
    if "@" not in spec:
        return False
    version = spec.rsplit("@", 1)[1]
    return bool(
        re.fullmatch(
            r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?",
            version,
        )
    )


def active_plugin_specs(path=MANIFEST_PATH, environ=None):
    manifest = load_plugin_manifest(path)
    environ = os.environ if environ is None else environ
    gated = manifest.get("gated", {})
    return [
        spec
        for spec in manifest["plugins"]
        if gated.get(spec) is None or environ.get(gated[spec], "0") == "1"
    ]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--print-specs", action="store_true")
    parser.add_argument("--active", action="store_true")
    args = parser.parse_args()
    if args.print_specs:
        specs = (
            active_plugin_specs(args.manifest)
            if args.active
            else plugin_specs(args.manifest)
        )
        print("\n".join(specs))
