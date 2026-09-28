"""Load the canonical OpenCode plugin manifest."""

import json
import argparse
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
    if any("@" not in item or item.rsplit("@", 1)[1] == "latest" for item in plugins):
        raise ValueError(
            "OpenCode plugin manifest entries must be pinned package@version specs"
        )
    return manifest


def plugin_specs(path=MANIFEST_PATH):
    return load_plugin_manifest(path)["plugins"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--print-specs", action="store_true")
    args = parser.parse_args()
    if args.print_specs:
        print("\n".join(plugin_specs(args.manifest)))
