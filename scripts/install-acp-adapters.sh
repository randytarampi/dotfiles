#!/usr/bin/env bash
set -euo pipefail

# Compatibility entrypoint for callers that still invoke the historical path.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/install-acp-adapters.py" "$@"
