#!/usr/bin/env python3
"""Generate or validate self-contained review prompts from the canonical skill."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "configs/skills/randy-review/SKILL.md"
REFERENCES = SOURCE.parent / "references"
PROMPT = "configs/review/code-review-prompt.md"
GITHUB = ".github/skills/code-review/SKILL.md"
OPENCODE = ".opencode/skills/code-review/SKILL.md"
MANIFEST = ROOT / "configs/review/assets-manifest.json"
FACETS = ("engineering", "facilitation", "mentoring", "philosophy")
START = "## Purpose and success criterion"


def generated() -> dict[str, str]:
    source = SOURCE.read_text(encoding="utf-8")
    if source.count(START) != 1:
        raise ValueError("Canonical skill must contain one common method marker")
    common = START + source.split(START, 1)[1]
    references = "\n\n---\n\n".join(
        (REFERENCES / f"{facet}.md").read_text(encoding="utf-8").strip()
        for facet in FACETS
    )
    bundle = (
        f"{common}\n\n<!-- START BUNDLED FACET REFERENCES: generated from "
        "configs/skills/randy-review/references/ -->\n\n"
        f"{references}\n\n<!-- END BUNDLED FACET REFERENCES -->\n"
    )
    return {
        PROMPT: (
            "# Pull request review\n\n"
            "<!-- GENERATED PORTABLE COPY; canonical entry point: "
            "configs/skills/randy-review/SKILL.md. Do not edit independently. -->\n\n"
            f"{bundle}"
        ),
        GITHUB: (
            "---\nname: code-review\n"
            'description: "Composable engineering review, discussion facilitation and mentoring for GitHub-hosted changes."\n'
            "---\n\n# Code review\n\n"
            "<!-- GENERATED PORTABLE COPY of configs/skills/randy-review/SKILL.md and references. -->\n\n"
            f"{bundle}"
        ),
        OPENCODE: (
            "---\nname: code-review\n"
            'description: "Composable engineering review, discussion facilitation and mentoring for OpenCode."\n'
            "---\n\n# Code review\n\n"
            "<!-- GENERATED PORTABLE COPY of configs/skills/randy-review/SKILL.md and references. -->\n\n"
            f"{bundle}"
        ),
    }


def check_bundles(expected: dict[str, str], assets: dict[str, str]) -> list[str]:
    """Return all source/bundle and trusted-digest discrepancies."""
    errors = []
    for relative, output in expected.items():
        path = ROOT / relative
        digest = hashlib.sha256(output.encode("utf-8")).hexdigest()
        if not path.is_file() or path.read_text(encoding="utf-8") != output:
            errors.append(f"Bundle out of sync: {relative}")
        if relative in assets and assets[relative] != digest:
            errors.append(f"Manifest out of sync: {relative}")
    return errors


def write_bundles(expected: dict[str, str], data: dict) -> None:
    """Update only generated files and existing manifest entries."""
    for relative, output in expected.items():
        (ROOT / relative).write_text(output, encoding="utf-8")
        if relative in data["assets"]:
            data["assets"][relative] = hashlib.sha256(
                output.encode("utf-8")
            ).hexdigest()
    MANIFEST.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Verify generated bundles")
    mode.add_argument("--write", action="store_true", help="Regenerate bundles")
    args = parser.parse_args()
    expected = generated()
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if "assets" not in data:
        raise ValueError("Missing assets in manifest")
    if args.write:
        write_bundles(expected, data)
        print("Updated review bundles and trusted asset manifest")
        return 0
    errors = check_bundles(expected, data["assets"])
    if errors:
        print("\n".join(errors))
        return 1
    print("Review bundles match canonical method and references")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
