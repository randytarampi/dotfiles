#!/usr/bin/env python3
"""Hermetic LiteLLM retention diagnostic and fixture-backed acceptance harness."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess  # nosec B404 - runs this repository's own configuration script
import sys
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
TARGET_ALIAS = "ollama-local/llama3.2:3b"
SAFE_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT")
BASELINE_PATHS = (
    ".local/share/litellm/config.yaml",
    ".local/share/litellm/config.models.json",
)


def validate_snapshot(
    config_path,
    ledger_path,
    notes_path,
    *,
    config_text=None,
    ledger_text=None,
    notes_text=None,
    previous_ledger_text=None,
    inventory_names=None,
    required_baseline_refs=(),
):
    """Validate generated aliases against independent baseline inventories."""
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
    previous = (
        json.loads(previous_ledger_text) if previous_ledger_text is not None else ledger
    )
    previous_names = {
        item.get("model_name") for item in previous if isinstance(item, dict)
    }
    served = set()
    for line in config_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- model_name:"):
            try:
                served.add(json.loads(stripped.partition(":")[2].strip()))
            except json.JSONDecodeError:
                continue
    inventory_names = set(inventory_names) if inventory_names is not None else None
    return {
        "ledger_retained_exactly": previous_names <= served and ledger_names == served,
        "target_present": TARGET_ALIAS in served,
        "unknown_reported": any("UNKNOWN" in str(note) for note in notes),
        "served_within_inventory": (
            True if inventory_names is None else served <= inventory_names
        ),
        "required_baseline_refs_served": set(required_baseline_refs) <= served,
        "served_names": served,
        "ledger_names": ledger_names,
        "notes": notes,
    }


def _deny_non_loopback(request, *args, **kwargs):
    url = request.full_url if hasattr(request, "full_url") else str(request)
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    if host not in ("localhost", "127.0.0.1", "::1"):
        raise RuntimeError(f"non-loopback network blocked: {host or url}")
    raise RuntimeError("loopback HTTP is not permitted by the smoke harness")


def run_smoke(
    home=None,
    mode="retention",
    dry_run=False,
    *,
    baseline=None,
    inventory=None,
    runner=None,
    network_hook=None,
):
    if mode not in ("retention", "acceptance"):
        raise ValueError(f"unknown mode: {mode}")
    # Safe-destination guard at entry (P0.4): fires for BOTH modes before any
    # early-skip or copy path can touch the destination.
    if home is not None:
        _destination = Path(home).expanduser().resolve()
        if _destination == Path.home().resolve():
            raise ValueError("--home must not be the real source HOME")
        if _destination.exists() and any(_destination.iterdir()):
            raise ValueError("--home must point at an empty directory")
    if mode == "retention":
        root = Path.home() / ".local/share/litellm"
        sources = [root / "config.yaml", root / "config.models.json"]
        if not all(path.is_file() for path in sources):
            return {
                "skipped": True,
                "reason": "deployed LiteLLM config.yaml or config.models.json baseline is missing",
            }
        inventory = {
            item["model_name"]
            for item in json.loads(sources[1].read_text())
            if isinstance(item, dict)
        }
        baseline = sources
    else:
        if baseline is None or inventory is None:
            raise ValueError(
                "acceptance requires complete baseline and local-model inventory inputs"
            )
        baseline = [Path(path) for path in baseline]
        if len(baseline) != 2 or not all(path.is_file() for path in baseline):
            raise ValueError(
                "acceptance requires both baseline config and ledger files"
            )
        inventory = set(inventory)
        if TARGET_ALIAS not in inventory:
            raise ValueError(f"local model inventory must include {TARGET_ALIAS}")
        if home is None:
            raise ValueError(
                "acceptance requires --home pointing at a new empty directory"
            )
    temporary_home = None
    if home is None:
        temporary_home = tempfile.TemporaryDirectory(prefix="local-ai-smoke-")
        home_path = Path(temporary_home.name)
    else:
        home_path = Path(home).expanduser().resolve()
        # Safe destination in BOTH modes: never the source HOME, never a
        # pre-populated destination an overwrite could damage (retention
        # copies baselines into the isolated root just like acceptance).
        if home_path == Path.home().resolve():
            raise ValueError("--home must not be the real source HOME")
        if home_path.exists() and any(home_path.iterdir()):
            raise ValueError("--home must point at an empty directory")
        if mode == "acceptance" and (
            home_path.exists() or not home_path.parent.is_dir()
        ):
            raise ValueError(
                "acceptance --home must not exist and its parent must exist"
            )
    try:
        if dry_run:
            return {
                "dry_run": True,
                "mode": mode,
                "command": [sys.executable, str(SCRIPTS / "configure-litellm.py")],
                "home": str(home_path),
            }
        command = [sys.executable, str(SCRIPTS / "configure-litellm.py")]
        if mode == "acceptance":
            home_path.mkdir()
        isolated_root = home_path / ".local/share/litellm"
        isolated_root.mkdir(parents=True, exist_ok=True)
        previous_ledger_text = baseline[1].read_text(encoding="utf-8")
        for source in baseline:
            shutil.copy2(source, isolated_root / source.name)
        environment = {key: os.environ[key] for key in SAFE_ENV if key in os.environ}
        environment.update(
            {
                "HOME": str(home_path),
                "DOTFILES_RUN_LITELLM_SETUP": "1",
                "DOTFILES_USE_LITELLM_PROXY": "0",
                "http_proxy": "http://127.0.0.1:9",
                "https_proxy": "http://127.0.0.1:9",
                "HTTP_PROXY": "http://127.0.0.1:9",
                "HTTPS_PROXY": "http://127.0.0.1:9",
            }
        )
        if mode == "acceptance":
            hook = network_hook or _deny_non_loopback
            # Ensure the child Python process enforces the same outbound policy.
            policy_dir = home_path / ".local-ai-smoke-policy"
            policy_dir.mkdir(parents=True)
            (policy_dir / "sitecustomize.py").write_text(
                "import socket, urllib.request\n"
                "def deny(request, *args, **kwargs):\n"
                "    url = request.full_url if hasattr(request, 'full_url') else str(request)\n"
                "    from urllib.parse import urlparse\n"
                "    host = (urlparse(url).hostname or '').lower()\n"
                "    if host not in ('localhost', '127.0.0.1', '::1'):\n"
                "        raise RuntimeError('non-loopback network blocked: ' + (host or url))\n"
                "    raise RuntimeError('loopback HTTP is not permitted by the smoke harness')\n"
                "urllib.request.urlopen = deny\n"
                "_connect = socket.socket.connect\n"
                "def guarded_connect(sock, address):\n"
                "    host = address[0] if isinstance(address, tuple) else address\n"
                "    if host not in ('localhost', '127.0.0.1', '::1'):\n"
                "        raise RuntimeError('non-loopback network blocked: ' + str(host))\n"
                "    return _connect(sock, address)\n"
                "socket.socket.connect = guarded_connect\n",
                encoding="utf-8",
            )
            environment["PYTHONPATH"] = str(policy_dir)
            # The assertion is executable evidence, not a check of proxy settings.
            try:
                hook("https://example.com")
            except RuntimeError as error:
                if "blocked" not in str(error):
                    raise
            else:
                raise RuntimeError("network hook did not reject non-loopback request")
        result = (
            runner(
                command,
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            if runner
            else subprocess.run(
                command,
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
        )
        report = validate_snapshot(
            isolated_root / "config.yaml",
            isolated_root / "config.models.json",
            isolated_root / "last_generation_notes.json",
            previous_ledger_text=previous_ledger_text,
            inventory_names=inventory if mode == "acceptance" else None,
            required_baseline_refs={
                item.get("model_name")
                for item in json.loads(previous_ledger_text)
                if isinstance(item, dict)
            },
        )
        report.update(
            {
                "mode": mode,
                "command_exit_zero": result.returncode == 0,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )
        if mode == "acceptance":
            # No-op generation guard: a run whose child wrote nothing new is
            # validating the copied baseline, not a cold start (P0.4).
            produced_config = (isolated_root / "config.yaml").read_text(
                encoding="utf-8"
            )
            report["generation_produced_new_config"] = produced_config != Path(
                baseline[0]
            ).read_text(encoding="utf-8")
        report["passed"] = (
            report["command_exit_zero"]
            and report.get("generation_produced_new_config", True)
            and report["ledger_retained_exactly"]
            and report["target_present"]
            and report["served_within_inventory"]
            and report["required_baseline_refs_served"]
        )
        return report
    finally:
        if temporary_home is not None:
            temporary_home.cleanup()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument(
        "--mode", choices=("retention", "acceptance"), default="retention"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--home", type=Path)
    parser.add_argument("--baseline-config", type=Path)
    parser.add_argument("--baseline-inventory", type=Path)
    args = parser.parse_args(argv)
    if args.mode == "acceptance" and not (
        args.baseline_config and args.baseline_inventory
    ):
        print(
            "FAIL: acceptance requires --baseline-config and --baseline-inventory",
            file=sys.stderr,
        )
        return 1
    try:
        report = run_smoke(
            args.home,
            mode=args.mode,
            dry_run=args.dry_run,
            baseline=(
                (args.baseline_config, args.baseline_inventory)
                if args.mode == "acceptance"
                else None
            ),
            inventory=(
                {
                    item.get("model_name")
                    for item in json.loads(
                        args.baseline_inventory.read_text(encoding="utf-8")
                    )
                    if isinstance(item, dict)
                }
                if args.mode == "acceptance"
                else None
            ),
        )
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    if report.get("skipped"):
        print(f"SKIP {report['reason']}")
        return 0
    if report.get("dry_run"):
        print(f"DRY RUN {args.mode}: no files copied or created")
        return 0
    print(
        json.dumps(
            {key: value for key, value in report.items() if not isinstance(value, set)},
            default=list,
        )
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
