#!/usr/bin/env python3
"""
Discover Models Helper.
Lists local Ollama models and prints them as a JSON array with name and size_gb.
"""

import sys
import json
import argparse
import os
import subprocess
import shutil
import re


def find_ollama() -> str:
    ollama_path = shutil.which("ollama")
    if ollama_path:
        return ollama_path
    try:
        result = subprocess.run(
            ["brew", "--prefix"], capture_output=True, text=True, timeout=5
        )
        brew_prefix = result.stdout.strip()
        if brew_prefix:
            candidate = os.path.join(brew_prefix, "bin", "ollama")
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    except Exception:
        pass
    return ""


def parse_size_gb(size_str: str) -> float:
    """Parse '4.0 GB', '512 MB', '21 GB' etc. to float GB."""
    match = re.search(r"(\d+(?:\.\d+)?)\s*([KMG]B)", size_str, re.IGNORECASE)
    if not match:
        return 0.0
    size = float(match.group(1))
    unit = match.group(2).upper()
    if unit == "GB":
        return size
    if unit == "MB":
        return size / 1024.0
    if unit == "KB":
        return size / 1048576.0
    return 0.0


def list_local_ollama_models() -> list:
    ollama_bin = find_ollama()
    if not ollama_bin:
        return []
    try:
        result = subprocess.run(
            [ollama_bin, "list"], capture_output=True, text=True, timeout=5
        )
        lines = result.stdout.splitlines()
        models = []
        for line in lines[1:]:
            parts = line.split()
            if len(parts) >= 3:
                name = parts[0]
                if name.endswith(":cloud") or name.endswith("-cloud"):
                    continue
                models.append(
                    {"name": name, "size_gb": parse_size_gb(" ".join(parts[2:4]))}
                )
            elif parts:
                name = parts[0]
                if name.endswith(":cloud") or name.endswith("-cloud"):
                    continue
                models.append({"name": name, "size_gb": 0.0})
        return models
    except Exception:
        return []


def main():
    parser = argparse.ArgumentParser(
        description="Prints a JSON array of local Ollama models with name and size_gb."
    )
    parser.parse_args()

    # If stdin is not a TTY (data is piped), read from stdin
    if not sys.stdin.isatty():
        models = []
        for line in sys.stdin:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, dict) and "name" in parsed:
                    models.append(
                        {
                            "name": parsed.get("name", ""),
                            "size_gb": float(parsed.get("size_gb", 0.0) or 0.0),
                        }
                    )
                    continue
            except Exception:
                pass
            models.append({"name": stripped, "size_gb": 0.0})
        print(json.dumps(models))
    else:
        # Otherwise, discover directly by running ollama
        models = list_local_ollama_models()
        print(json.dumps(models))


if __name__ == "__main__":
    main()
