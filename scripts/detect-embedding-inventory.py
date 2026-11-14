#!/usr/bin/env python3
"""Print a redacted, read-only embedding inventory when explicitly enabled."""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from embedding_inventory import collect_inventory, read_state
from openwebui import OpenWebUIClient


def main(argv=None, *, config_provider=None, receipt_reader=None, environ=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and print the same read-only report",
    )
    if argv is not None:
        parser.parse_args(argv)
    elif environ is not None:
        pass  # programmatic invocation: flags are defaulted
    else:
        parser.parse_args()
    env = os.environ if environ is None else environ
    if env.get("DOTFILES_RUN_OPENWEBUI_EMBEDDING_INVENTORY", "0") != "1":
        print(
            "Embedding inventory disabled; set DOTFILES_RUN_OPENWEBUI_EMBEDDING_INVENTORY=1 to enable."
        )
        return 0
    if config_provider is None:
        base_url = env.get("OPENWEBUI_BASE_URL", "http://127.0.0.1:8080")
        api_key = env.get("OPENWEBUI_API_KEY", "")
        client = OpenWebUIClient(base_url, api_key)
        config_provider = client.get_embedding_config
    if receipt_reader is None:
        state_path = Path(
            os.path.expanduser("~/.local/share/openwebui/data/managed-embedding.json")
        )

        def receipt_reader():
            try:
                return read_state(state_path)
            except ValueError as error:
                return {"state": "unknown", "reason": str(error)}

    report = collect_inventory(config_provider, receipt_reader)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
