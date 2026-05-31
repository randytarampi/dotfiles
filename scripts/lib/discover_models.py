#!/usr/bin/env python3
"""
Discover Models Helper.
Lists local Ollama models and prints them as a JSON array of non-empty strings.
"""

import sys
import json
import argparse
import os
import subprocess
import shutil


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
            if parts:
                models.append(parts[0])
        return models
    except Exception:
        return []


def main():
    parser = argparse.ArgumentParser(
        description="Prints a JSON array of local Ollama models."
    )
    parser.parse_args()

    # If stdin is not a TTY (data is piped), read from stdin
    if not sys.stdin.isatty():
        models = []
        for line in sys.stdin:
            stripped = line.strip()
            if stripped:
                models.append(stripped)
        print(json.dumps(models))
    else:
        # Otherwise, discover directly by running ollama
        models = list_local_ollama_models()
        print(json.dumps(models))


if __name__ == "__main__":
    main()
