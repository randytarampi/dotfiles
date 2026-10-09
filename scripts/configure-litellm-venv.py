#!/usr/bin/env python3
"""Apply the narrow LiteLLM /v1/models Hugging Face enrichment bypass."""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess  # nosec B404 - used for a fixed interpreter and repository-owned source path.
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))

import logger  # noqa: E402
from cli_helpers import add_common_args  # noqa: E402

BEGIN = "# dotfiles listing-enrichment bypass: begin HF fast path"
END = "# dotfiles listing-enrichment bypass: end HF fast path"
EXPECTED_DEF = "def _safe_get_model_info(model: str, get_model_info: Callable[[str], ModelInfo]) -> ModelInfo | None:"
GUARD = """    if isinstance(model, str) and model.startswith("huggingface/"):
        return {"key": model, "litellm_provider": "huggingface", "mode": "chat", "max_input_tokens": None, "max_output_tokens": None}"""


def target_file():
    root = Path(os.environ.get("LITELLM_ROOT", "~/.local/share/litellm")).expanduser()
    candidates = sorted(
        glob.glob(str(root / "venv/lib/python3*/site-packages/litellm/proxy/utils.py"))
    )
    return Path(candidates[-1]) if candidates else None


def patched_source(source):
    if BEGIN in source or END in source:
        definition = source.find(EXPECTED_DEF)
        function_end = definition + len(EXPECTED_DEF) if definition >= 0 else -1
        begin = source.find(BEGIN)
        guard = source.find(
            'if isinstance(model, str) and model.startswith("huggingface/"):'
        )
        end = source.find(END)
        if all(position >= 0 for position in (function_end, begin, guard, end)) and (
            function_end < begin < guard < end
        ):
            return source
        raise ValueError(
            "incomplete patch; delete sentinels or reinstall litellm and re-run"
        )
    lines = source.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if re.match(r"^def _safe_get_model_info\s*\(", line):
            if line.rstrip("\r\n") != EXPECTED_DEF:
                raise ValueError(
                    f"unexpected _safe_get_model_info definition: {line.strip()}"
                )
            signature = (
                line.split("(", 1)[1].rsplit(")", 1)[0]
                if "(" in line and ")" in line
                else ""
            )
            if not re.search(r"\bmodel\b", signature) or not line.rstrip().endswith(
                ":"
            ):
                raise ValueError(
                    f"unexpected _safe_get_model_info signature: {line.strip()}"
                )
            insert = index + 1
            if insert < len(lines) and lines[insert].lstrip().startswith(
                ('"""', "'''")
            ):
                quote = lines[insert].lstrip()[:3]
                if lines[insert].count(quote) < 2:
                    insert += 1
                    while insert < len(lines) and quote not in lines[insert]:
                        insert += 1
                    if insert == len(lines):
                        raise ValueError("unterminated function docstring")
                insert += 1
            block = f"    {BEGIN}\n{GUARD}\n    {END}\n"
            lines.insert(insert, block)
            return "".join(lines)
    raise ValueError("_safe_get_model_info definition not found")


def cost_map_file():
    """Locate the installed bundled model cost map (same venv as the patcher)."""
    root = Path(os.environ.get("LITELLM_ROOT", "~/.local/share/litellm")).expanduser()
    candidates = sorted(
        glob.glob(
            str(
                root / "venv/lib/python3*/site-packages/litellm/"
                "model_prices_and_context_window_backup.json"
            )
        )
    )
    return Path(candidates[-1]) if candidates else None


def snapshot_rates():
    """Authoritative checked-in rates (configs/litellm/model-rates.json)."""
    snapshot_path = SCRIPT_DIR.parent / "configs" / "litellm" / "model-rates.json"
    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError(f"unreadable model-rates snapshot: {snapshot_path}")
    models = snapshot.get("models") if isinstance(snapshot, dict) else None
    if not isinstance(models, dict) or not models:
        raise ValueError("model-rates snapshot has no models")
    return {str(key): value for key, value in models.items() if isinstance(value, dict)}


def cost_map_rate_drift(installed, snapshot):
    """Entries whose installed value differs from the snapshot (or are absent)."""
    drift = [
        model_id
        for model_id, entry in sorted(snapshot.items())
        if installed.get(model_id) != entry
    ]
    return drift


def merge_cost_rates(path, *, dry_run=False, no_backup=False):
    """Merge the checked-in claude rates into the installed LiteLLM cost map.

    The bundled backup file shipped with pinned LiteLLM 1.102.1 lacks the
    claude-5.x entries, and the service intentionally reads ONLY this file
    (launchd runs single-worker in an internet-scrubbed environment, so the
    remote map fetch is unavailable). Request-time cost tracking resolves ids
    from this map, hence spend rows for Claude wires record $0 today. This is
    the JSON analogue of the listing patch: snapshot-verified, idempotent,
    first-write backed up, byte-rollback on write failure.
    """
    snapshot = snapshot_rates()
    original_bytes = path.read_bytes()
    original_mode = path.stat().st_mode
    try:
        installed = json.loads(original_bytes.decode("utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"installed cost map is not valid JSON: {error}")
    if not isinstance(installed, dict):
        raise ValueError("installed cost map has unexpected JSON shape")
    drift = cost_map_rate_drift(installed, snapshot)
    if not drift:
        logger.info("LiteLLM cost-map rates already current: %s", path)
        return 0
    if dry_run:
        logger.info(
            "Would merge %d rate entry/ies into %s: %s",
            len(drift),
            path,
            ", ".join(drift),
        )
        return 0
    merged = dict(installed)
    for model_id, entry in snapshot.items():
        merged[model_id] = entry
    payload = json.dumps(merged, indent=4, sort_keys=True) + "\n"
    backup = path.with_name(path.name + ".orig-dotfiles")
    if not no_backup and not backup.exists():
        shutil.copy2(path, backup)
    try:
        json.loads(payload)  # never write an unparseable map
        temp = path.with_name(path.name + ".rollback")
        temp.write_text(payload, encoding="utf-8")
        os.chmod(temp, original_mode)
        os.replace(temp, path)
    except Exception:
        restored = False
        try:
            temp = path.with_name(path.name + ".rollback")
            temp.write_bytes(original_bytes)
            os.chmod(temp, original_mode)
            os.replace(temp, path)
            restored = True
        finally:
            if not restored:
                logger.warning(
                    "LiteLLM cost-map merge failed; could not restore original file"
                )
        if restored:
            logger.warning("LiteLLM cost-map merge failed; restored original file")
        return 1
    logger.info(
        "Merged %d rate entry/ies into the LiteLLM cost map: %s", len(drift), path
    )
    return 0


def configure(path, *, dry_run=False, no_backup=False):
    original_bytes = path.read_bytes()
    source = original_bytes.decode("utf-8")
    original_mode = path.stat().st_mode
    updated = patched_source(source)
    if updated == source:
        logger.info("LiteLLM venv listing bypass already applied: %s", path)
        return 0
    if dry_run:
        logger.info("Would patch LiteLLM _safe_get_model_info in %s", path)
        logger.info(
            "Would create backup %s", path.with_name(path.name + ".orig-dotfiles")
        )
        return 0
    backup = path.with_name(path.name + ".orig-dotfiles")
    if not no_backup and not backup.exists():
        shutil.copy2(path, backup)
    try:
        path.write_text(updated, encoding="utf-8")
        # Resolve the interpreter from the venv root independently of site-packages depth.
        venv = path.parents[4]
        while venv != venv.parent and venv.name != "venv":
            venv = venv.parent
        python = venv / "bin" / "python"
        if not python.is_file():
            python = Path(sys.executable)
        result = subprocess.run(  # nosec B603 - interpreter and file path are resolved from the managed venv.
            [str(python), "-m", "py_compile", str(path)], check=False
        )
        if result.returncode:
            raise RuntimeError("LiteLLM venv patch compile check failed")
    except Exception:
        restored = False
        try:
            temp = path.with_name(path.name + ".rollback")
            temp.write_bytes(original_bytes)
            os.chmod(temp, original_mode)
            os.replace(temp, path)
            restored = True
        finally:
            if not restored:
                logger.warning(
                    "LiteLLM venv patch failed; could not restore original file"
                )
        if restored:
            logger.warning("LiteLLM venv patch failed; restored original file")
        return 1
    logger.info("Applied LiteLLM venv listing bypass: %s", path)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    add_common_args(parser, no_backup=True)
    args = parser.parse_args()
    path = target_file()
    if not path or not path.is_file():
        logger.info("LiteLLM proxy/utils.py not found under configured venv; skipping")
        return 0
    patch_result = 0
    try:
        patch_result = configure(path, dry_run=args.dry_run, no_backup=args.no_backup)
    except (OSError, ValueError) as error:
        logger.warning("Could not safely patch LiteLLM venv: %s", error)
        return 1
    if patch_result:
        return patch_result
    rate_map = cost_map_file()
    if not rate_map or not rate_map.is_file():
        logger.info(
            "LiteLLM bundled cost map not found under configured venv; skipping rates merge"
        )
        return 0
    try:
        return merge_cost_rates(
            rate_map, dry_run=args.dry_run, no_backup=args.no_backup
        )
    except (OSError, ValueError) as error:
        logger.warning("Could not safely merge LiteLLM cost rates: %s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
