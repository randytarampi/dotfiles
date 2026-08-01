#!/usr/bin/env python3
"""Verify iTerm2 config integrity."""

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))
import logger

REQUIRED_PROFILE_KEYS = {
    "Command",
    "Custom Command",
    "Guid",
    "Name",
    "Rewritable",
    "Working Directory",
}
STRING_PROFILE_KEYS = {
    "Command",
    "Custom Command",
    "Custom Directory",
    "Description",
    "Guid",
    "Name",
    "Non Ascii Font",
    "Normal Font",
    "Shortcut",
    "Terminal Type",
    "Working Directory",
}


def check_path(path: Path, label: str) -> bool:
    """Check a config path, including broken symlinks."""
    if path.is_symlink() and not path.exists():
        logger.error(f"{label}: stale symlink target: {path}")
        return False
    if not path.exists():
        logger.error(f"{label}: missing file: {path}")
        return False
    logger.info(f"{label}: present: {path}")
    return True


def validate_profile_json(path: Path) -> bool:
    """Validate the checked-in iTerm2 profile JSON."""
    if not path.exists():
        if path.is_symlink():
            logger.warning(f"iTerm2 JSON is a stale symlink: {path}")
        else:
            logger.warning(
                f"iTerm2 JSON source is absent: {path} (the .tmpl is the canonical source)"
            )
        return True
    logger.info(f"iTerm2 JSON: present: {path}")

    valid = True
    try:
        with path.open(encoding="utf-8") as config_file:
            data = json.load(config_file)
    except json.JSONDecodeError as exc:
        logger.error(f"iTerm2 JSON is malformed: {path}: {exc}")
        return False
    except OSError as exc:
        logger.error(f"Could not read iTerm2 JSON {path}: {exc}")
        return False

    if not isinstance(data, dict):
        logger.error("iTerm2 JSON top level must be an object")
        return False
    profiles = data.get("Profiles")
    if not isinstance(profiles, list) or not profiles:
        logger.error("iTerm2 JSON must contain a non-empty 'Profiles' list")
        return False
    logger.info(f"iTerm2 JSON parsed successfully ({len(profiles)} profile(s))")

    for index, profile in enumerate(profiles):
        label = f"iTerm2 profile {index}"
        if not isinstance(profile, dict):
            logger.error(f"{label} must be an object")
            valid = False
            continue

        missing = REQUIRED_PROFILE_KEYS - profile.keys()
        if missing:
            logger.error(f"{label} missing required keys: {', '.join(sorted(missing))}")
            valid = False

        for key in STRING_PROFILE_KEYS:
            if key in profile and not isinstance(profile[key], str):
                logger.error(
                    f"{label} '{key}' must be a string, got {type(profile[key]).__name__}"
                )
                valid = False
        for key in ("Command", "Custom Command"):
            if (
                key in profile
                and isinstance(profile[key], str)
                and not profile[key].strip()
            ):
                logger.error(f"{label} '{key}' must not be empty")
                valid = False
        if "Rewritable" in profile and not isinstance(profile["Rewritable"], bool):
            logger.error(f"{label} 'Rewritable' must be a boolean")
            valid = False

        for key in ("Working Directory", "Log Directory", "Background Image Location"):
            value = profile.get(key)
            if isinstance(value, str) and value.strip():
                candidate = Path(os.path.expanduser(value))
                if candidate.is_absolute() and not candidate.exists():
                    logger.warning(f"{label} {key} path does not exist: {candidate}")

        command = profile.get("Command")
        if isinstance(command, str) and command.strip():
            try:
                executable = shlex.split(command)[0]
            except ValueError as exc:
                logger.error(f"{label} 'Command' cannot be parsed: {exc}")
                valid = False
            else:
                if os.path.isabs(executable):
                    if not os.access(executable, os.X_OK):
                        logger.warning(
                            f"{label} command executable is unavailable: {executable}"
                        )
                elif shutil.which(executable) is None:
                    logger.warning(
                        f"{label} command is unavailable on PATH: {executable}"
                    )

    rewritable = os.access(path, os.W_OK)
    if rewritable:
        logger.info(f"iTerm2 JSON is writable: {path}")
    else:
        logger.warning(f"iTerm2 JSON is not writable by the current user: {path}")
    return valid


def template_is_well_formed(path: Path) -> bool:
    """Perform a deliberately small, dependency-free chezmoi syntax check."""
    if not check_path(path, "iTerm2 template"):
        return False
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error(f"Could not read iTerm2 template {path}: {exc}")
        return False

    if content.count("{{") != content.count("}}"):
        logger.error(f"iTerm2 template has unbalanced template delimiters: {path}")
        return False
    if "{{{" in content or "}}}" in content:
        logger.error(f"iTerm2 template has malformed template delimiters: {path}")
        return False
    logger.info(f"iTerm2 template syntax looks valid: {path}")
    return True


def render_and_validate_template(path: Path) -> bool:
    """Render the template with chezmoi when available and parse its JSON."""
    chezmoi = shutil.which("chezmoi")
    if chezmoi is None:
        logger.warning(
            "chezmoi not found; skipping rendered iTerm2 template validation"
        )
        return True

    try:
        result = subprocess.run(
            [chezmoi, "execute-template"],
            input=path.read_text(encoding="utf-8"),
            text=True,
            capture_output=True,
            check=False,
            cwd=path.parents[2],
        )
    except OSError as exc:
        logger.error(f"Could not execute chezmoi for iTerm2 template: {exc}")
        return False
    if result.returncode != 0:
        detail = result.stderr.strip() or "unknown chezmoi error"
        logger.error(f"iTerm2 template failed to render: {detail}")
        return False
    try:
        rendered = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        logger.error(f"Rendered iTerm2 template is not valid JSON: {exc}")
        return False
    if not isinstance(rendered, dict) or not isinstance(rendered.get("Profiles"), list):
        logger.error("Rendered iTerm2 template has an invalid profile structure")
        return False
    logger.info("Rendered iTerm2 template is valid JSON")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify iTerm2 config integrity.")
    parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    json_path = repo_root / "configs/iterm2/Default.json"
    template_path = repo_root / "configs/iterm2/Default.json.tmpl"

    checks = [
        validate_profile_json(json_path),
        template_is_well_formed(template_path),
    ]
    if checks[1]:
        checks.append(render_and_validate_template(template_path))
    if all(checks):
        logger.info("iTerm2 verification passed")
        return 0
    logger.error("iTerm2 verification failed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
