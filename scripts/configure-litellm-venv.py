#!/usr/bin/env python3
"""Apply the narrow LiteLLM /v1/models Hugging Face enrichment bypass."""

import argparse
import glob
import os
import re
import shutil
import subprocess
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
        match = (
            re.match(re.escape(EXPECTED_DEF), source[definition:])
            if definition >= 0
            else None
        )
        if match and BEGIN in source and END in source:
            end = source.find(END, source.find(BEGIN))
            region = source[definition + match.end() : end]
            guard = (
                'if isinstance(model, str) and model.startswith("huggingface/"):'
                in region
            )
            if guard and source.find(BEGIN) < source.find(END):
                return source
        raise ValueError(
            "incomplete patch; delete sentinels or reinstall litellm and re-run"
        )
    lines = source.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if re.match(r"^def _safe_get_model_info\s*\(", line):
            if line.rstrip("\n") != EXPECTED_DEF:
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


def configure(path, *, dry_run=False, no_backup=False):
    source = path.read_text(encoding="utf-8")
    original_bytes = source.encode("utf-8")
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
        result = subprocess.run(
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
    try:
        return configure(path, dry_run=args.dry_run, no_backup=args.no_backup)
    except (OSError, ValueError) as error:
        logger.warning("Could not safely patch LiteLLM venv: %s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
