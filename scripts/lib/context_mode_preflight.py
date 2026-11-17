"""Read-only Context Mode activation preflight for the OpenCode plugin installer.

The installer precedes config generation in the chezmoi apply sequence. An
unconfigured plugin may still activate from the OpenCode package cache, so
requiring a previously staged disabled config closes that first-install gap.
"""

import json
import sys
from pathlib import Path

REQUIRED_EFFECTS = {
    "ctx_execute": "ask",
    "ctx_batch": "ask",
    "ctx_index": "ask",
    "ctx_purge": "ask",
    "ctx_upgrade": "deny",
}


def safely_staged(path: Path) -> bool:
    """True only when the plugin is disabled or already securely configured."""
    if path.is_symlink() or not path.is_file():
        return False
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return False
    if not isinstance(config, dict):
        return False
    plugins = config.get("plugins")
    if not isinstance(plugins, list):
        return False

    enabled = [
        item
        for item in plugins
        if isinstance(item, str) and item.startswith("context-mode@")
    ]
    disabled = plugins.count("-context-mode")
    if disabled == 1 and not enabled:
        return True
    if disabled or enabled != ["context-mode@2.2.1"]:
        return False

    permissions = config.get("permissions")
    if not isinstance(permissions, list):
        return False
    for action, expected in REQUIRED_EFFECTS.items():
        last = next(
            (
                rule
                for rule in reversed(permissions)
                if isinstance(rule, dict)
                and rule.get("action") in (action, "*")
                and rule.get("resource") == "*"
            ),
            None,
        )
        if last is None or last.get("effect") != expected:
            return False
    return True


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(2)
    sys.exit(0 if safely_staged(Path(sys.argv[1])) else 1)
