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
    if manifest.get("version") != 2:
        raise ValueError("OpenCode plugin manifest version must be 2")
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
    cli_plugins = manifest.get("cli_plugins")
    if not isinstance(cli_plugins, list) or not all(
        isinstance(item, dict)
        and set(item) == {"package"}
        and isinstance(item["package"], str)
        for item in cli_plugins
    ):
        raise ValueError(
            "OpenCode plugin manifest cli_plugins must contain package-only objects"
        )
    cli_specs = [item["package"] for item in cli_plugins]
    if len(set(cli_specs)) != len(cli_specs):
        raise ValueError(
            "OpenCode plugin manifest contains duplicate cli_plugins packages"
        )
    overlap = set(plugins) & set(cli_specs)
    if overlap:
        # A TUI-capable plugin must never be co-registered: plugins in
        # opencode.json auto-load in the TUI, and cli.json duplicates would
        # double-load them (docs/OPENCODE.md).
        raise ValueError(
            "OpenCode plugin manifest must not repeat a package across plugins "
            f"and cli_plugins: {sorted(overlap)}"
        )
    if any(not is_pinned_spec(item) for item in [*plugins, *cli_specs]):
        raise ValueError(
            "OpenCode plugin manifest entries must be pinned package@version specs"
        )
    return manifest


def plugin_specs(path=MANIFEST_PATH):
    return load_plugin_manifest(path)["plugins"]


def cli_plugin_specs(path=MANIFEST_PATH):
    return [item["package"] for item in load_plugin_manifest(path)["cli_plugins"]]


def active_cli_plugin_specs(path=MANIFEST_PATH, environ=None):
    return cli_plugin_specs(path)


def is_pinned_spec(spec: str) -> bool:
    if spec.startswith("github:"):
        return bool(
            re.fullmatch(r"github:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#[A-Za-z0-9]+", spec)
        )
    if "@" not in spec:
        return False
    version = spec.rsplit("@", 1)[1]
    if version == "latest":
        # Floating tracking alias: OpenCode resolves the newest release at
        # install/update time; re-resolve only changes by running the gated
        # `opencode plugin update` (probed on the v2.2.5 CLI, 2026-10-31:
        # `plugin add` dedupes and does NOT refresh an existing resolution,
        # `plugin check` prints resolved concrete versions). Allowed
        # deliberately; see CONVENTIONS.md.
        return True
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


def configured_plugin_specs(path=MANIFEST_PATH, environ=None):
    """Return plugin specs in the order emitted in native OpenCode config."""
    plugins = active_plugin_specs(path, environ)
    return plugins


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
