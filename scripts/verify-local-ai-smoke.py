#!/usr/bin/env python3
"""Hermetic offline-retention smoke for the deployed LiteLLM catalogue."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess  # nosec B404 - the harness drives this repo's own scripts only
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
TARGET_ALIAS = "ollama-cloud/glm-5.3-flash"


def validate_snapshot(
    config_path,
    ledger_path,
    notes_path,
    config_text=None,
    ledger_text=None,
    notes_text=None,
    previous_ledger_text=None,
):
    """Check generated YAML aliases/ledger and generation notes, no network."""
    config_text = (
        config_text
        if config_text is not None
        else config_path.read_text(encoding="utf-8")
    )
    ledger = json.loads(
        ledger_text
        if ledger_text is not None
        else ledger_path.read_text(encoding="utf-8")
    )
    notes = json.loads(
        notes_text if notes_text is not None else notes_path.read_text(encoding="utf-8")
    )
    ledger_names = {item.get("model_name") for item in ledger if isinstance(item, dict)}
    previous_ledger = (
        json.loads(previous_ledger_text) if previous_ledger_text is not None else ledger
    )
    previous_names = {
        item.get("model_name") for item in previous_ledger if isinstance(item, dict)
    }
    config_names = set()
    for line in config_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- model_name:"):
            raw = stripped.partition(":")[2].strip()
            try:
                config_names.add(json.loads(raw))
            except json.JSONDecodeError:
                continue
    return {
        "ledger_retained_exactly": previous_names <= config_names
        and ledger_names == config_names,
        "target_present": TARGET_ALIAS in config_names and TARGET_ALIAS in ledger_names,
        "unknown_reported": any("UNKNOWN" in str(note) for note in notes),
        "no_invented_models": config_names <= ledger_names,
        "ledger_names": ledger_names,
        "config_names": config_names,
        "notes": notes,
    }


def run_smoke(home=None, mode="gateway", dry_run=False):
    source_home = Path.home()
    source_root = source_home / ".local/share/litellm"
    config_source = source_root / "config.yaml"
    ledger_source = source_root / "config.models.json"
    if not all(path.is_file() for path in (config_source, ledger_source)):
        return {
            "skipped": True,
            "reason": "deployed LiteLLM config.yaml or config.models.json is missing",
        }

    temporary_home = None
    if home is None:
        temporary_home = tempfile.TemporaryDirectory(prefix="local-ai-smoke-")
        home_path = Path(temporary_home.name)
    else:
        home_path = Path(home).expanduser().resolve()
    try:
        isolated_root = home_path / ".local/share/litellm"
        isolated_root.mkdir(parents=True, exist_ok=True)
        previous_ledger_text = ledger_source.read_text(encoding="utf-8")
        for source in (config_source, ledger_source):
            shutil.copy2(source, isolated_root / source.name)
        command = [sys.executable, str(SCRIPTS / "configure-litellm.py")]
        if dry_run:
            print(
                f"Plan: {' '.join(command)} (HOME={home_path}, mode={mode}, WAN blocked)"
            )
            return {"dry_run": True, "stdout": "", "stderr": ""}
        environment = {
            **os.environ,
            "HOME": str(home_path),
            "DOTFILES_RUN_LITELLM_SETUP": "1",
            "DOTFILES_USE_LITELLM_PROXY": "1" if mode == "gateway" else "0",
            "https_proxy": "http://127.0.0.1:9",
            "http_proxy": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "HTTP_PROXY": "http://127.0.0.1:9",
        }
        result = subprocess.run(  # nosec B603 - repo script under a WAN-blocked, disposable HOME
            command,
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        notes_path = isolated_root / "last_generation_notes.json"
        report = validate_snapshot(
            isolated_root / "config.yaml",
            isolated_root / "config.models.json",
            notes_path,
            previous_ledger_text=previous_ledger_text,
        )
        report["command_exit_zero"] = result.returncode == 0
        report["unknown_or_expired_warning"] = any(
            "UNKNOWN" in str(note) or "absent/expired" in str(note)
            for note in report["notes"]
        )
        report["mode"] = mode
        report["stderr"] = result.stderr
        report["stdout"] = result.stdout
        report["passed"] = all(
            report[key]
            for key in (
                "command_exit_zero",
                "ledger_retained_exactly",
                "target_present",
                "unknown_or_expired_warning",
                "no_invented_models",
            )
        )
        return report
    finally:
        if temporary_home is not None:
            temporary_home.cleanup()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--home", type=Path)
    parser.add_argument("--direct", action="store_true")
    parser.add_argument("--gateway", action="store_true")
    args = parser.parse_args(argv)
    if args.direct and args.gateway:
        parser.error("--direct and --gateway are mutually exclusive")
    mode = "direct" if args.direct else "gateway"
    try:
        report = run_smoke(args.home, mode=mode, dry_run=args.dry_run)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    if report.get("skipped"):
        print(f"SKIP {report['reason']}")
        return 0
    if report.get("dry_run"):
        return 0
    labels = (
        ("command_exit_zero", "configure-litellm exits 0"),
        ("ledger_retained_exactly", "previous model ledger retained exactly"),
        ("target_present", "ollama-cloud/glm-5.3-flash present"),
        ("unknown_or_expired_warning", "UNKNOWN/absent-expired warning present"),
        ("no_invented_models", "no model absent from ledger was invented"),
    )
    for key, label in labels:
        print(f"{'PASS' if report[key] else 'FAIL'} {label}")
    print(f"Mode: {mode}")
    print(
        f"Drift summary: config={len(report['config_names'])} ledger={len(report['ledger_names'])}"
    )
    if report["stdout"]:
        print(report["stdout"].rstrip())
    if report["stderr"]:
        print(report["stderr"].rstrip(), file=sys.stderr)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
